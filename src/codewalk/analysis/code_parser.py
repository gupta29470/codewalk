"""
=============================================================================
 code_parser.py — Tree-Sitter AST Parsing (Extract Functions & Classes)
=============================================================================

WHAT THIS FILE DOES:
    Parses source code into an Abstract Syntax Tree (AST) using tree-sitter,
    then extracts all function and class definitions with their:
      - Name (e.g., "login", "AuthService")
      - Type ("function" or "class")
      - Line range (start_line, end_line)
      - Source code (the actual text)
      - Parameters (for functions: ["username", "password"])

HOW IT WORKS:
    1. Pick the tree-sitter grammar for the language (Python, Dart, JS, etc.)
    2. Feed the file bytes into tree-sitter → get a Concrete Syntax Tree (CST)
    3. Walk the tree looking for specific node types:
       - Python: "function_definition", "class_definition"
       - JS/TS: "function_declaration", "class_declaration"
       - Dart: "function_signature", "class_definition"
    4. Extract the name, parameters, and source text from each matching node
    5. Return a list of structured dicts

REAL-WORLD ANALOGY:
    Like a table of contents generator for code.
    Given a file, it produces: "This file has: login() on lines 5-20,
    AuthService class on lines 22-100, validate_token() on lines 102-115."

WHY TREE-SITTER (not regex)?
    - Regex can't handle nested brackets, multi-line definitions, decorators
    - Tree-sitter understands ACTUAL syntax — never fooled by strings/comments
    - One approach works for 14 languages (just different grammar + node types)
    - Fast: parses a 10,000-line file in <50ms

WHERE IT'S CALLED:
    - chunker.py → chunk_file_with_parser() uses parse_file() to find functions
    - pipeline.py → also used directly for symbol-level analysis

DEPENDENCIES:
    - tree-sitter: The parsing engine (C library with Python bindings)
    - tree_sitter_python/javascript/etc.: Language grammar packages
      (each is a separate pip package like "tree-sitter-python")

=============================================================================
"""

# ─── Imports ─────────────────────────────────────────────────────────

import logging
import importlib  # Dynamic import of grammar packages

from tree_sitter import Language, Parser  # The core parsing engine

from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")


# =============================================================================
# GRAMMAR_MAP — Language Name → Grammar Package Name
# =============================================================================
# Maps our internal language names to pip package names.
# Each package provides a compiled grammar for tree-sitter.
#
# To add a new language:
#   1. pip install tree-sitter-{language}
#   2. Add entry here: "language_name": "tree_sitter_{language}"
#   3. Add NODE_TYPES entry below (which AST nodes are functions/classes)

GRAMMAR_MAP = {
    "python":     "tree_sitter_python",
    "javascript": "tree_sitter_javascript",
    "typescript": "tree_sitter_typescript",
    "dart":       "tree_sitter_dart",
    "java":       "tree_sitter_java",
    "go":         "tree_sitter_go",
    "rust":       "tree_sitter_rust",
    "ruby":       "tree_sitter_ruby",
    "c":          "tree_sitter_c",
    "cpp":        "tree_sitter_cpp",
    "csharp":     "tree_sitter_c_sharp",
    "php":        "tree_sitter_php",
    "kotlin":     "tree_sitter_kotlin",
    "swift":      "tree_sitter_swift",
}

# Cache loaded Language objects (expensive to load, reuse across calls)
_language_cache = {}


# =============================================================================
# NODE_TYPES — What AST Nodes Represent Functions/Classes Per Language
# =============================================================================
# Each language has different node type names in its grammar.
# This dict tells the parser WHAT to look for in each language's AST.
#
# FIELDS:
#   "function": list of node types that represent function-like things
#   "class": list of node types that represent class/struct-like things
#   "name_field": which child field contains the symbol's name
#   "params_field": which child field contains function parameters

NODE_TYPES = {
    "python": {
        "function": ["function_definition"],
        "class": ["class_definition"],
        "name_field": "name",           # def NAME(...):
        "params_field": "parameters"    # def name(PARAMS):
    },
    "javascript": {
        "function": ["function_declaration", "method_definition"],
        "class": ["class_declaration"],
        "name_field": "name",
        "params_field": "formal_parameters"
    },
    "typescript": {
        "function": ["function_declaration", "method_definition"],
        "class": ["class_declaration"],
        "name_field": "name",
        "params_field": "formal_parameters"
    },
    "dart": {
        # Dart uses "signature" nodes, not "declaration" (grammar-specific)
        "function": ["function_signature", "method_signature"],
        "class": ["class_definition"],
        "name_field": "name",
        "params_field": "formal_parameter_list"
    },
    "java": {
        "function": ["method_declaration", "constructor_declaration"],
        "class": ["class_declaration", "interface_declaration"],
        "name_field": "name",
        "params_field": "formal_parameters"
    },
    "go": {
        "function": ["function_declaration", "method_declaration"],
        "class": [],  # Go has no classes — just structs and interfaces
        "name_field": "name",
        "params_field": "parameters"
    },
    "rust": {
        "function": ["function_item"],
        "class": ["struct_item", "impl_item", "enum_item"],
        "name_field": "name",
        "params_field": "parameters"
    },
    "ruby": {
        "function": ["method"],
        "class": ["class"],
        "name_field": "name",
        "params_field": "method_parameters"
    },
    "c": {
        "function": ["function_definition"],
        "class": ["struct_specifier"],
        "name_field": "declarator",     # C uses "declarator" not "name"
        "params_field": "parameters"
    },
    "cpp": {
        "function": ["function_definition"],
        "class": ["class_specifier", "struct_specifier"],
        "name_field": "declarator",
        "params_field": "parameters"
    },
    "csharp": {
        "function": ["method_declaration", "constructor_declaration"],
        "class": ["class_declaration", "interface_declaration"],
        "name_field": "name",
        "params_field": "parameter_list"
    },
    "php": {
        "function": ["function_definition", "method_declaration"],
        "class": ["class_declaration", "interface_declaration"],
        "name_field": "name",
        "params_field": "formal_parameters",
    },
    "kotlin": {
        "function": ["function_declaration"],
        "class": ["class_declaration", "object_declaration"],
        "name_field": "name",
        "params_field": "function_value_parameters",
    },
    "swift": {
        "function": ["function_declaration"],
        "class": ["class_declaration", "struct_declaration", "enum_declaration"],
        "name_field": "name",
        "params_field": "parameter_list",
    },
}


# =============================================================================
# Grammar Loading Functions
# =============================================================================

def get_language(language: str):
    """Load a tree-sitter Language object for the given language name.

    CACHING:
        First call: imports the grammar package → creates Language object → caches it
        Subsequent calls: returns cached object instantly

    SPECIAL CASES:
        - TypeScript: package has TWO grammars (TS + TSX). We use language_typescript()
        - PHP: package uses language_php() instead of language()

    Returns None if grammar not installed (pip package missing).

    EXAMPLE:
        get_language("go")
          model_name = GRAMMAR_MAP["go"] = "tree_sitter_go"
          grammar_module = importlib.import_module("tree_sitter_go")
          lang = Language(grammar_module.language())  # compiled C grammar object
          _language_cache["go"] = lang
          returns <Language: go>

        get_language("go")  # second call
          "go" in _language_cache → True
          returns cached <Language: go> instantly

        get_language("brainfuck")
          GRAMMAR_MAP.get("brainfuck") = None
          returns None
    """
    if language in _language_cache:
        return _language_cache[language]

    model_name = GRAMMAR_MAP.get(language)
    if not model_name:
        return None

    try:
        # Dynamically import the grammar package (e.g., import tree_sitter_python)
        grammar_module = importlib.import_module(model_name)

        if language == "typescript":
            # TypeScript grammar package exposes language_typescript() not language()
            lang = Language(grammar_module.language_typescript())
        elif language == "php":
            # PHP grammar package exposes language_php() not language()
            lang = Language(grammar_module.language_php())
        else:
            # Standard: all other packages expose language()
            lang = Language(grammar_module.language())

        _language_cache[language] = lang
        return lang

    except (ImportError, AttributeError):
        # Grammar package not installed or doesn't have expected function
        return None


def get_parser_for_language(language: str):
    """Create a tree-sitter Parser loaded with the right grammar.

    The Parser is the engine that converts source bytes → syntax tree.
    Each parser is configured with one language grammar.

    Returns None if language not supported.
    """
    lang = get_language(language)
    if not lang:
        return None
    return Parser(lang)


# =============================================================================
# Name & Parameter Extraction Helpers
# =============================================================================

def extract_name(node, name_field: str) -> str:
    """Pull the name out of a function/class AST node.

    STANDARD CASE:
        node.child_by_field_name("name") → "login"

    FALLBACK 1 (C/C++):
        C grammar nests the name inside a function_declarator node.
        The name is at: node → function_declarator → declarator → "main"

    FALLBACK 2 (Dart):
        Dart's method_signature wraps a function_signature or getter_signature.
        The actual name is one level deeper.

    Returns "<anonymous>" if name can't be found (rare edge case).

    EXAMPLES:
        Go: func Fprint(w io.Writer, ...)
          node = <function_declaration>
          name_field = "name"
          node.child_by_field_name("name") = <identifier: "Fprint">
          returns "Fprint"

        C: int main(int argc, char** argv) { ... }
          node = <function_definition>
          name_field = "declarator"
          node.child_by_field_name("declarator") = None (nested)
          Fallback 1: child.type == "function_declarator" → YES
            inner = child.child_by_field_name("declarator") = <identifier: "main">
          returns "main"
    """
    # Standard: direct child field
    name_node = node.child_by_field_name(name_field)
    if name_node:
        return name_node.text.decode("utf-8")

    # Fallback for C/C++: name nested inside function_declarator
    for child in node.children:
        if child.type == "function_declarator":
            inner = child.child_by_field_name("declarator")
            if inner:
                return inner.text.decode("utf-8")

    # Fallback for Dart: name inside inner signature node
    for child in node.children:
        if child.type in ("function_signature", "getter_signature"):
            inner_name = child.child_by_field_name(name_field)
            if inner_name:
                return inner_name.text.decode("utf-8")

    return "<anonymous>"


def extract_params(node, params_field: str) -> list[str]:
    """Pull parameter names from a function AST node.

    EXAMPLES:
        def login(username, password): → ["username", "password"]
        function add(a: number, b: number) → ["a", "b"]

    HOW IT WORKS:
        1. Find the parameters node (contains all param children)
        2. For each param child (skip punctuation like parens/commas):
           - Try child_by_field_name("name") → direct name
           - Try matching "identifier" type → the param IS an identifier
           - Fallback: search children for an identifier node
    """
    # Try direct field access
    params_node = node.child_by_field_name(params_field)

    if not params_node:
        # Fallback: check inside child nodes (e.g., Dart method_signature)
        for child in node.children:
            params_node = child.child_by_field_name(params_field)
            if params_node:
                break
        if not params_node:
            return []

    param_names = []

    for child in params_node.children:
        # Skip syntax tokens: ( ) ,
        if child.type in ("(", ")", ",", "comment"):
            continue

        # Method 1: param node has a "name" field
        name_node = child.child_by_field_name("name")
        if name_node:
            param_names.append(name_node.text.decode("utf-8"))
        # Method 2: param IS an identifier (e.g., Python simple params)
        elif child.type == "identifier":
            param_names.append(child.text.decode("utf-8"))
        else:
            # Method 3: look for identifier child (e.g., typed_parameter)
            for sub in child.children:
                if sub.type == "identifier":
                    param_names.append(sub.text.decode("utf-8"))
                    break

    return param_names


# =============================================================================
# walk_tree() — Recursive AST Traversal
# =============================================================================

def walk_tree(node, target_types: set, skip_children_types: set = None):
    """Recursively walk the syntax tree, yield nodes matching target types.

    WHY NOT JUST USE node.children DIRECTLY?
        Functions can be nested: a class contains methods, a function contains
        inner functions. We need to traverse the ENTIRE tree depth-first.

    skip_children_types:
        If a matched node's type is in this set, don't recurse into it.
        This prevents duplicates in Dart where method_signature contains
        function_signature — without this, we'd yield both for one function.

    EXAMPLE TREE (Python):
        module
         ├── class_definition (name="Auth")       ← YIELD THIS
         │    ├── function_definition (name="login")  ← YIELD THIS
         │    └── function_definition (name="logout") ← YIELD THIS
         └── function_definition (name="main")    ← YIELD THIS
    """
    if skip_children_types is None:
        skip_children_types = set()

    if node.type in target_types:
        yield node
        if node.type in skip_children_types:
            return  # Don't recurse into this node's children

    for child in node.children:
        yield from walk_tree(child, target_types, skip_children_types)


# =============================================================================
# parse_file() — The Main Entry Point
# =============================================================================

def parse_file(file_path: str, language: str) -> list[dict]:
    """Parse a source file → list of extracted functions and classes.

    EXECUTION FLOW:
        1. Get parser for language (loads grammar if needed)
        2. Get NODE_TYPES for language (what to look for)
        3. Read file as bytes (tree-sitter works with raw bytes, not str)
        4. Parse → syntax tree
        5. Walk tree → find all function/class nodes
        6. For each match: extract name, line range, source code, params
        7. Return list of structured dicts

    RETURN FORMAT:
        [
            {
                "type": "function",
                "name": "login",
                "start_line": 5,
                "end_line": 20,
                "code": "def login(username, password):\n    ...",
                "args": ["username", "password"]  ← only for functions
            },
            {
                "type": "class",
                "name": "AuthService",
                "start_line": 22,
                "end_line": 100,
                "code": "class AuthService:\n    ..."
            }
        ]

    Returns empty list if:
        - Language not supported (no grammar)
        - File can't be read (permission denied, not found)
        - No functions/classes found (file is just imports/constants)

    EXAMPLE TRACE (fatih/color, file "color.go", language "go"):
        parser = get_parser_for_language("go") → Parser with Go grammar
        node_types = NODE_TYPES["go"] = {
            "function": ["function_declaration", "method_declaration"],
            "class": [],
            "name_field": "name",
            "params_field": "parameters"
        }
        source = Path("color.go").read_bytes() → 12,847 bytes
        tree = parser.parse(source) → full AST

        Walk tree looking for function_declaration / method_declaration nodes:
            node = <function_declaration: "New"> at line 60-75
              item_type = "function", name = "New"
              start_line = 60, end_line = 75
              code = "func New(value ...Attribute) *Color { ... }"
              args = ["value"]

            node = <method_declaration: "Add"> at line 80-85
              item_type = "function", name = "Add"
              start_line = 80, end_line = 85

        returns 24 items (24 functions, 0 classes — Go has no classes)
    """
    # Step 1: Get parser (returns None if language unsupported)
    parser = get_parser_for_language(language)
    if not parser:
        return []

    # Step 2: Get node type config for this language
    node_types = NODE_TYPES.get(language)
    if not node_types:
        return []

    # Step 3: Read file as bytes (tree-sitter requires bytes, not str)
    try:
        with open(file_path, "rb") as file:
            source = file.read()
    except (FileNotFoundError, IOError, PermissionError):
        return []

    # Step 4: Parse source → syntax tree
    tree = parser.parse(source)

    # Decode source to string for extracting code text by line
    lines = source.decode("utf-8", errors="replace").splitlines()

    # Step 5: Build set of all AST node types we want to find
    function_types = set(node_types["function"])
    class_types = set(node_types["class"])
    all_target_types = function_types | class_types

    items = []

    # Step 6: Walk tree, extract each matching node
    # skip_children_types=function_types prevents double-counting nested functions
    for node in walk_tree(tree.root_node, all_target_types, function_types):
        # Classify: is this a function or a class?
        if node.type in function_types:
            item_type = "function"
        else:
            item_type = "class"

        # tree-sitter uses 0-indexed lines; we use 1-indexed
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1

        # Extract the symbol's name
        name = extract_name(node, node_types["name_field"])

        # Extract the source code for this node (join relevant lines)
        code = "\n".join(lines[start_line - 1 : end_line])

        item = {
            "type": item_type,
            "name": name,
            "start_line": start_line,
            "end_line": end_line,
            "code": code,
        }

        # Only extract parameters for functions (classes don't have params)
        if item_type == "function":
            item["args"] = extract_params(node, node_types["params_field"])

        items.append(item)

    return items
