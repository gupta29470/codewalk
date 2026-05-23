"""
=============================================================================
 call_extractor.py — Symbol-Level Call Site Extraction
=============================================================================

WHAT THIS FILE DOES:
    1. Walks the tree-sitter AST of each source file.
    2. Finds every function/method CALL in the code.
    3. Records WHO called WHAT at WHICH line number.
    4. Returns UNRESOLVED data — callee is just a name like "print",
       not yet matched to a specific symbol. Resolution happens later
       in graph_store._populate_symbol_calls().

HOW IT WORKS (3 steps):
    1. Parse: Get a tree-sitter AST for the file (reuses code_parser grammars)
    2. Walk: Iterative DFS with scope tracking — at each node, we know
       which function/class we're currently INSIDE
    3. Collect: When we hit a "call_expression" node → extract the callee
       name and record (caller_scope, callee_name, line)

REAL-WORLD ANALOGY:
    Imagine reading a book and highlighting every time the author says
    "see chapter X" or "as described in section Y". You write down:
    - Which chapter you're currently reading (scope/caller)
    - Which chapter they reference (callee)
    - Which page number (line)
    That's exactly what this file does for code.

WHY ITERATIVE DFS (not recursion)?
    Python's default recursion limit is 1000 frames. A deeply nested
    AST (e.g., a 10,000-line file) can exceed that. Iterative DFS with
    an explicit stack has no limit.

WHERE IT'S CALLED:
    - graph_store.py → _populate_symbol_calls() calls extract_calls_batch()

DEPENDENCIES:
    - code_parser: Provides tree-sitter parsers and language-specific node types
=============================================================================
"""

# ─── Imports ─────────────────────────────────────────────────────────

# logging: Reports how many calls were extracted from how many files
import logging

# code_parser provides:
#   get_parser_for_language() → tree-sitter parser for any supported language
#   NODE_TYPES → which AST node types are functions/classes in each language
#   extract_name() → get the name of a function/class definition node
from src.codewalk.analysis.code_parser import (
    get_parser_for_language, NODE_TYPES, extract_name
)

logger = logging.getLogger("codewalk")


# =============================================================================
# Language-Specific Call Node Types
# =============================================================================

# Each language represents function calls with different AST node types.
# Python uses "call", JavaScript uses "call_expression", Dart splits into
# "function_expression_invocation" vs "method_invocation", etc.
CALL_TYPES: dict[str, list[str]] = {
    "python":     ["call"],
    "javascript": ["call_expression"],
    "typescript": ["call_expression"],
    "dart":       ["function_expression_invocation", "method_invocation"],
    "java":       ["method_invocation"],
    "go":         ["call_expression"],
    "rust":       ["call_expression"],
    "ruby":       ["call", "method_call"],
    "c":          ["call_expression"],
    "cpp":        ["call_expression"],
    "csharp":     ["invocation_expression"],
    "php":        ["function_call_expression", "method_call_expression"],
    "kotlin":     ["call_expression"],
    "swift":      ["call_expression"],
}

# =============================================================================
# Callee Name Extraction Helpers
# =============================================================================

# When we see foo() → the "function" child of the call node is an identifier.
# When we see obj.method() → the "function" child is a member_expression,
# and we need to drill into it to get "method" (the rightmost name).

# Fields to check for the "function being called" part of a call node.
# Different languages use different field names in their grammars.
_FUNCTION_FIELDS = ("function", "name", "method")

# Fields to check for the actual name WITHIN a member expression.
# obj.method → "method" is in the "property" or "attribute" field.
_NAME_FIELDS = ("property", "attribute", "field", "name")

# Member access node types across languages.
# When the callee is obj.method(), the "function" child will be one of these.
# We then drill into it to extract just "method" (the rightmost identifier).
_MEMBER_TYPES = frozenset({
    "attribute",                 # Python:  obj.method
    "member_expression",         # JS/TS:   obj.method
    "selector_expression",       # Go:      pkg.Func
    "field_expression",          # Rust/C:  obj.method
    "member_access_expression",  # C#:      obj.Method
    "scoped_identifier",         # Rust:    mod::func
    "qualified_name",            # PHP:     Ns\func
    "navigation_expression",     # Kotlin:  obj.method
})


# =============================================================================
# Core Extraction Functions
# =============================================================================

def _extract_callee_name(call_node) -> str | None:
    """Extract the callee function/method name from a call expression node.

    Handles common patterns across all languages:
        foo()           → "foo"
        self.foo()      → "foo"
        obj.method()    → "method"
        Mod.func()      → "func"
        pkg::func()     → "func"
    """
    func_node = None
    for field in _FUNCTION_FIELDS:
        func_node = call_node.child_by_field_name(field)
        if func_node is not None:
            break
    
    if func_node is None:
        for child in call_node.children:
            if child.type in ("identifier", "simple_identifier"):
                return child.text.decode("utf-8")
        return None
    
    if func_node.type in (
        "identifier", "simple_identifier",
        "property_identifier", "field_identifier",
    ):
        return func_node.text.decode("utf-8")
    
    if func_node.type in _MEMBER_TYPES:
        for field in _NAME_FIELDS:
            name_child = func_node.child_by_field_name(field)
            if name_child is not None:
                return name_child.text.decode("utf-8")
            
        for child in reversed(func_node.children):
            if child.type in (
                "identifier", "simple_identifier",
                "property_identifier", "field_identifier", "name",
            ):
                return child.text.decode("utf-8")
            
    return None


# ── File-Level Extraction ────────────────────────────────────────────

def extract_calls_from_file(file_path: str, language: str, identifier_path: str | None = None) -> list[dict]:
    """Extract all call sites from a source file.

    Args:
        file_path: Path to read the file from (can be absolute).
        language: Language identifier (python, go, etc.).
        identifier_path: Path to use in qualified names. Defaults to file_path.

    Returns list of dicts::

        {
            "caller": "path/file.py:function_name",   # qualified name
            "callee_name": "other_function",           # unresolved name
            "line": 42                                 # call site line
        }

    Scope tracking:
        - Calls inside a function → caller is that function
        - Calls inside a class but outside methods → caller is the class
        - Calls at module level → caller is "file_path:<module>"

    Uses iterative DFS (no recursion limit issues on large files).

    EXAMPLE TRACE (Go file color.go, function Fprint calls setWriter at line 289):

        tree.root_node = <source_file> (the entire Go file's AST)
        stack starts as: [(<source_file>, "color.go:<module>")]

        DFS iteration 1:
            node = <source_file>, scope = "color.go:<module>"
            node.type = "source_file" → not a call, not a def
            Push all children with scope "color.go:<module>"

        DFS iteration N (inside func Fprint definition):
            node = <function_declaration: "Fprint">, scope = "color.go:<module>"
            node.type = "function_declaration" → IS in all_def_types
            name = extract_name(node, name_field) = "Fprint"
            current_scope = "color.go:Fprint"  (← scope changed!)
            Push children with scope "color.go:Fprint"

        DFS iteration N+5 (the setWriter() call inside Fprint):
            node = <call_expression: "setWriter(w)">, scope = "color.go:Fprint"
            node.type = "call_expression" → IS in call_type_set
            callee = _extract_callee_name(node) = "setWriter"
            caller_short = "Fprint"  (from "color.go:Fprint".rsplit(":")[-1])
            callee != caller_short? "setWriter" != "Fprint" → YES (not recursive)
            line = node.start_point[0] + 1 = 289
            key = ("color.go:Fprint", "setWriter", 289)
            → results.append({"caller": "color.go:Fprint", "callee_name": "setWriter", "line": 289})
    """
    parser = get_parser_for_language(language)
    if parser is None:
        return []
    
    node_config = NODE_TYPES.get(language)
    call_types = CALL_TYPES.get(language)

    if not node_config or not call_types:
        return []
    
    try:
        with open(file_path, "rb") as f:
            source = f.read()
    except (FileNotFoundError, IOError, PermissionError) as e:
        logger.warning(f"Could not read file {file_path}: {e}")
        return []
    
    tree = parser.parse(source)

    call_type_set = set(call_types)
    function_types = set(node_config["function"])
    class_types = set(node_config["class"])
    all_def_types = function_types | class_types
    name_field = node_config["name_field"]

    results = []
    seen: set[tuple[str, str, int]] = set()  # dedup: (caller, callee, line)

    id_path = identifier_path or file_path
    module_scope = f"{id_path}:<module>"
    stack = [(tree.root_node, module_scope)]

    while stack:
        node, scope = stack.pop()

        current_scope = scope
        if node.type in all_def_types:
            name = extract_name(node, name_field)
            current_scope = f"{id_path}:{name}"

        if node.type in call_type_set:
            callee = _extract_callee_name(node)
            if callee is not None:
                caller_short = current_scope.rsplit(":", 1)[-1]
                if callee != caller_short:
                    line = node.start_point[0] + 1
                    key = (current_scope, callee, line)
                    if key not in seen:
                        seen.add(key)
                        results.append({
                            "caller": current_scope,
                            "callee_name": callee,
                            "line": line,
                        })
        
        for child in reversed(node.children):
            stack.append((child, current_scope))
    
    return results


# ── Batch Extraction ─────────────────────────────────────────────────

def extract_calls_batch(
        files: list[dict]
) -> list[dict]:
    """Extract calls from all files that have tree-sitter support.

    Args:
        files: From scan_directory() — [{"file_path": str, "language": str, ...}]

    Returns:
        Combined list of call dicts from all files.
    """
    all_calls = []
    parsed = 0
    skipped = 0

    for file_info in files:
        language = file_info.get("language", "")
        if language not in CALL_TYPES:
            skipped += 1
            continue

        read_path = file_info.get("absolute_path", file_info["file_path"])
        calls = extract_calls_from_file(read_path, language, identifier_path=file_info["file_path"])
        all_calls.extend(calls)
        parsed += 1
    
    logger.info(
        f"[call_extractor] Extracted {len(all_calls)} call sites "
        f"from {parsed} files ({skipped} skipped — no grammar)"
    )
    return all_calls




