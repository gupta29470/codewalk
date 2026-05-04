import ast
from pathlib import Path

def parse_python_file(file_path: str) -> list[dict]:
    """Parse a Python file using AST → extract functions, classes, and their code."""
    try:
        source = Path(file_path).read_text(encoding="utf-8")
    except(UnicodeDecodeError, PermissionError):
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
                "decorators": [get_decorator_name(decorator) for decorator in node.decorator_list],
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
                 "methods": [body.name for body in node.body if isinstance(body, (ast.FunctionDef, ast.AsyncFunctionDef))]
            })
    
    return items

def get_source_segment(lines: list[str], start: int, end: int) -> str:
    """Extract source code lines (1-indexed to 0-indexed)"""
    return "\n".join(lines[start-1:end])

def get_decorator_name(node) -> str:
    """Get decorator name from AST node."""
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        return f"{get_name(node.value)}.{node.attr}"
    elif isinstance(node, ast.Call):
        return get_decorator_name(node.func)
    return ""

def get_name(node) -> str:
    """Get name string from various AST node types."""
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        return f"{get_name(node.value)}.{node.attr}"
    return ""