import importlib
from tree_sitter import Language, Parser

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

def parse_file(file_path: str, language: str) -> list[dict]:
    """Parse ANY supported language file → list of functions and classes."""
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

    items = []

    # Step 6: Walk the tree, collect matching nodes
    for node in walk_tree(tree.root_node, all_target_types, function_types):
        if node.type in function_types:
            item_type = "function"
        else:
            item_type = "class"

        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1

        name = extract_name(node, node_types["name_field"])
        code = "\n".join(lines[start_line - 1 : end_line])

        item = {
            "type": item_type,
            "name": name,
            "start_line": start_line,
            "end_line": end_line,
            "code": code,
        }

        if item_type == "function":
            item["args"] = extract_params(node, node_types["params_field"])

        items.append(item)
    
    return items