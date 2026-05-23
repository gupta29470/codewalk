"""
=============================================================================
 python_parser.py - Python-Specific AST Parser (stdlib ast module)
=============================================================================

WHAT THIS FILE DOES:
    Parses Python files using Python's built-in ast module to extract
    functions and classes with rich metadata (decorators, bases, methods).

    This is an ALTERNATIVE to tree-sitter for Python files only.
    Provides extra detail that tree-sitter doesn't easily give:
      - Decorators: @app.route("/login"), @property
      - Class bases: class AuthService(BaseService)
      - Method list: which methods belong to which class

HOW IT DIFFERS FROM code_parser.py:
    code_parser.py: tree-sitter, works for 14 languages, basic info
    python_parser.py: ast module, Python only, richer metadata

WHERE IT'S CALLED:
    - pipeline.py for Python-specific analysis (when extra detail needed)
    - Not used in the main chunking path (chunker uses code_parser.py)

DEPENDENCIES:
    - ast: Python stdlib (built-in, no install needed)
    - pathlib: file reading

=============================================================================
"""

# --- Imports ---

import ast
from pathlib import Path


# =============================================================================
# parse_python_file() - Main Entry Point
# =============================================================================

def parse_python_file(file_path: str) -> list[dict]:
    """Parse a Python file using stdlib ast -> extract functions and classes.

    Returns richer metadata than tree-sitter:
      - Functions: name, lines, code, decorators, args
      - Classes: name, lines, code, base classes, method names

    Returns empty list if file can't be read or has syntax errors.

    EXAMPLE TRACE (file: src/codewalk/config.py):
        source = Path("src/codewalk/config.py").read_text()
        tree = ast.parse(source)

        ast.walk(tree) yields nodes in order:

        node = <ClassDef: Settings> at line 15-45
          items.append({
              "type": "class",
              "name": "Settings",
              "start_line": 15, "end_line": 45,
              "code": "class Settings(BaseSettings): ...",
              "bases": ["BaseSettings"],
              "methods": ["model_post_init"]
          })

        node = <FunctionDef: get_llm> at line 50-62
          items.append({
              "type": "function",
              "name": "get_llm",
              "start_line": 50, "end_line": 62,
              "code": "def get_llm(model=None): ...",
              "decorators": [],
              "args": ["model"]
          })

        returns [{"type":"class","name":"Settings",...}, {"type":"function","name":"get_llm",...}]
    """
    try:
        source = Path(file_path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    lines = source.splitlines()
    items = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            items.append({
                "type": "function",
                "name": node.name,
                "start_line": node.lineno,
                "end_line": node.end_lineno,
                "code": get_source_segment(lines, node.lineno, node.end_lineno),
                "decorators": [get_decorator_name(d) for d in node.decorator_list],
                "args": [arg.arg for arg in node.args.args],
            })

        elif isinstance(node, ast.ClassDef):
            items.append({
                "type": "class",
                "name": node.name,
                "start_line": node.lineno,
                "end_line": node.end_lineno,
                "code": get_source_segment(lines, node.lineno, node.end_lineno),
                "bases": [get_name(base) for base in node.bases],
                "methods": [
                    body.name for body in node.body
                    if isinstance(body, (ast.FunctionDef, ast.AsyncFunctionDef))
                ],
            })

    return items


# =============================================================================
# Helper Functions
# =============================================================================

def get_source_segment(lines: list[str], start: int, end: int) -> str:
    """Extract source code lines. Converts 1-indexed line numbers to 0-indexed."""
    return "\n".join(lines[start - 1 : end])


def get_decorator_name(node) -> str:
    """Get decorator name from AST node.

    Handles:
        @property           -> "property"
        @app.route("/x")    -> "app.route"
        @lru_cache(128)     -> "lru_cache"
    """
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        return f"{get_name(node.value)}.{node.attr}"
    elif isinstance(node, ast.Call):
        return get_decorator_name(node.func)
    return ""


def get_name(node) -> str:
    """Get name string from various AST node types.

    Handles:
        ast.Name("Path")           -> "Path"
        ast.Attribute(x, "route")  -> "x.route"
    """
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        return f"{get_name(node.value)}.{node.attr}"
    return ""
