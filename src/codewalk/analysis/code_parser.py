"""Multi-language tree-sitter parser and symbol extraction utilities."""
import logging
import importlib
from tree_sitter import Language, Parser

from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")

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
    "kotlin": "tree_sitter_kotlin",
    "swift":  "tree_sitter_swift",
}

_language_cache = {}

NODE_TYPES = {
    "python": {
        "function": ["function_definition"],
        "class": ["class_definition"],
        "name_field": "name",
        "params_field": "parameters"
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
        "class": [],
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
        "name_field": "declarator",
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


def get_language(language: str):
    """Load a tree-sitter Language object for the given language name.
    Returns None if grammar not available."""
    if language in _language_cache:
        return _language_cache[language]

    model_name = GRAMMAR_MAP.get(language)

    if not model_name:
        return None

    try:
        grammar_module = importlib.import_module(model_name)

        if language == "typescript":
            # TWO grammars (TypeScript + TSX). It exposes language_typescript()
            lang = Language(grammar_module.language_typescript())
        elif language == "php":
            lang = Language(grammar_module.language_php())
        else:
            lang = Language(grammar_module.language())

        _language_cache[language] = lang

        return lang

    except(ImportError, AttributeError):
        return None

def get_parser_for_language(language: str):
    """Create a tree-sitter Parser loaded with the right grammar.
    Returns None if language not supported."""
    lang = get_language(language)

    if not lang:
        return None

    return Parser(lang)

def extract_name(node, name_field: str) -> str:
    """Pull the name out of a function/class node."""
    name_node = node.child_by_field_name(name_field)
    if name_node:
        return name_node.text.decode("utf-8")
    
    # Fallback for C/C++ where the name is nested inside a
    # function_declarator node (C grammar is more verbose)
    for child in node.children:
        if child.type == "function_declarator":
            inner = child.child_by_field_name("declarator")
            if inner:
                return inner.text.decode("utf-8")
            
    # Fallback for Dart: method_signature wraps function_signature
    # or getter_signature, which has the actual name
    for child in node.children:
        if child.type in ("function_signature", "getter_signature"):
            inner_name = child.child_by_field_name(name_field)
            if inner_name:
                return inner_name.text.decode("utf-8")
    
    return "<anonymous>"

def extract_params(node, params_field: str) -> list[str]:
    """Pull parameter names from a function node."""
    params_node = node.child_by_field_name(params_field)
    if not params_node:
        # Fallback: check inside child nodes (e.g. Dart method_signature)
        for child in node.children:
            params_node = child.child_by_field_name(params_field)
            if params_node:
                break
        
        if not params_node:
            return []

    param_names = []

    for child in params_node.children:
        if child.type in ("(", ")", ",", "comment"):
            continue

        name_node = child.child_by_field_name("name")
        if name_node:
            param_names.append(name_node.text.decode("utf-8"))
        elif child.type == "identifier":
            param_names.append(child.text.decode("utf-8"))
        else:
            # Fallback: look for an identifier inside the param node
            # (e.g. Python typed_parameter has identifier as a child)
            for sub in child.children:
                if sub.type == "identifier":
                    param_names.append(sub.text.decode("utf-8"))
                    break

    return param_names


def walk_tree(node, target_types: set, skip_children_types: set = None):
    """Recursively walk the CST and yield nodes matching target types.
    
    skip_children_types: if a matched node's type is in this set,
    don't recurse into its children. This prevents duplicates like
    Dart's method_signature containing function_signature.
    """

    if skip_children_types is None:
        skip_children_types = set()

    if node.type in target_types:
        yield node

        if node.type in skip_children_types:
            return

    for child in node.children:
        yield from walk_tree(child, target_types, skip_children_types)

def _extract_decorators(node) -> list[str]:
    """Collect decorator/annotation texts from a class/function node."""
    decorators: list[str] = []
    for child in node.children:
        if child.type in ("decorator", "annotation"):
            text = child.text.decode("utf-8", errors="replace").strip()
            if text.startswith("@"):
                text = text[1:].strip()
            decorators.append(text)
    return decorators


def _extract_identifier_names(node) -> list[str]:
    """Recursively collect identifier-like names from a type/base node."""
    names: list[str] = []
    seen: set[str] = set()
    target_types = {
        "identifier", "simple_identifier", "type_identifier",
        "property_identifier", "field_identifier", "name",
    }
    for child in walk_tree(node, target_types):
        name = child.text.decode("utf-8", errors="replace")
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _extract_class_parents(node, language: str) -> list[str]:
    """Extract superclass/base/interface names from a class node."""
    fields = ("bases", "superclass", "interfaces", "extended_types",
              "implemented_types", "base_class", "inheritance", "supertypes")
    names: list[str] = []
    for field in fields:
        base_node = node.child_by_field_name(field)
        if base_node is not None:
            names.extend(_extract_identifier_names(base_node))
    # Deduplicate while preserving order.
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _attach_parent_class_and_methods(items: list[dict]) -> list[dict]:
    """For function items inside class ranges, set parent_class and fill class methods."""
    class_ranges = [
        (i["start_line"], i["end_line"], i["name"])
        for i in items if i["type"] == "class"
    ]
    for item in items:
        if item["type"] != "function":
            continue
        for start, end, class_name in class_ranges:
            if start <= item["start_line"] and item["end_line"] <= end:
                item["parent_class"] = class_name
                break
        else:
            item["parent_class"] = None

    class_methods: dict[str, list[str]] = {}
    for item in items:
        if item["type"] == "function" and item.get("parent_class"):
            class_methods.setdefault(item["parent_class"], []).append(item["name"])
    for item in items:
        if item["type"] == "class":
            item.setdefault("methods", class_methods.get(item["name"], []))
    return items


def parse_file(file_path: str, language: str) -> list[dict]:
    """Parse ANY supported language file → list of functions and classes."""
    # Python AST path is more accurate and already extracts decorators/bases/methods.
    if language == "python":
        from src.codewalk.analysis.python_parser import parse_python_file
        items = parse_python_file(file_path)
        return _attach_parent_class_and_methods(items)

    # Step 1: Get a parser for this language
    parser = get_parser_for_language(language)
    if not parser:
        return []

    # Step 2: Get the node type mapping for this language
    node_types = NODE_TYPES.get(language)
    if not node_types:
        return []

    # Step 3: Read the file as bytes (tree-sitter works with bytes, not str)
    try:
        with open(file_path, "rb") as file:
            source = file.read()
    except (FileNotFoundError, IOError, PermissionError):
        return []

    # Step 4: Parse → get the syntax tree
    tree = parser.parse(source)

    lines = source.decode("utf-8", errors="replace").splitlines()

    # Step 5: Build set of all node types we care about
    function_types = set(node_types["function"])
    class_types = set(node_types["class"])
    all_target_types = function_types | class_types

    items: list[dict] = []

    # Step 6: Walk the tree, collect matching nodes
    for node in walk_tree(tree.root_node, all_target_types, function_types):
        is_function = node.type in function_types
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        name = extract_name(node, node_types["name_field"])
        code = "\n".join(lines[start_line - 1 : end_line])
        decorators = _extract_decorators(node)

        item: dict = {
            "type": "function" if is_function else "class",
            "name": name,
            "start_line": start_line,
            "end_line": end_line,
            "code": code,
            "decorators": decorators,
        }

        if is_function:
            item["args"] = extract_params(node, node_types["params_field"])
        else:
            item["bases"] = _extract_class_parents(node, language)
            item["methods"] = []

        items.append(item)

    return _attach_parent_class_and_methods(items)