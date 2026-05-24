import logging

from src.codewalk.analysis.code_parser import (
    get_parser_for_language, NODE_TYPES, extract_name
)

logger = logging.getLogger("codewalk")

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

_FUNCTION_FIELDS = ("function", "name", "method")

_NAME_FIELDS = ("property", "attribute", "field", "name")

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




