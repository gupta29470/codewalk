import logging
from pathlib import Path
from platform import node

from tree_sitter_cpp import language

from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")

from src.codewalk.analysis.code_parser import (
    get_parser_for_language,
    GRAMMAR_MAP
)

IMPORT_NODE_TYPES = {
    "python":     ["import_statement", "import_from_statement"],
    "javascript": ["import_statement", "call_expression"],
    "typescript": ["import_statement", "call_expression"],
    "dart":       ["import_or_export"],
    "java":       ["import_declaration"],
    "go":         ["import_declaration", "import_spec"],
    "rust":       ["use_declaration"],
    "ruby":       ["call"],       # require() / require_relative()
    "php":        ["namespace_use_declaration"],
    "c":          ["preproc_include"],
    "cpp":        ["preproc_include"],
    "csharp":     ["using_directive"],
    "kotlin": ["import"],
    "swift":  ["import_declaration"],
}

def extract_imports(file_path: str, language: str) -> list[str]:
    """Parse a file with tree-sitter and extract all import strings.

    Returns a list of raw import strings, e.g.:
        Python:  ["os", "pathlib.Path", "src.codewalk.config"]
        JS/TS:   ["express", "./auth_service"]
        Dart:    ["package:flutter/material.dart", "../models/user.dart"]
    """
    if language not in IMPORT_NODE_TYPES:
        return []
    
    parser = get_parser_for_language(language)
    if not parser:
        return []
    
    try:
        source = Path(file_path).read_bytes()
    except (FileNotFoundError, PermissionError):
        return []
    
    tree = parser.parse(source)
    root = tree.root_node

    target_types = set(IMPORT_NODE_TYPES[language])

    imports = []

    for node in _walk_for_imports(root, target_types):
        raw_import = _extract_raw_import(node, language)
        if raw_import:
            imports.append(raw_import)
    
    return imports

def _walk_for_imports(node, target_types):
    """Walk the AST and yield nodes whose type is in target_types."""
    if node.type in target_types:
        # For call_expression, only yield require() calls
        if node.type == "call_expression":
            func = node.child_by_field_name("function")
            if func and func.text.decode("utf-8") == "require":
                yield node
        else:
            yield node
    
    for child in node.children:
        yield from _walk_for_imports(child, target_types)  

def _extract_dart_import(node) -> str:
    """Walk: import_or_export → library_import → import_specification → configurable_uri → uri"""
    for child in node.children:
        if child.type == "library_import":
            for spec in child.children:
                if spec.type == "import_specification":
                    for part in spec.children:
                        if part.type == "configurable_uri":
                            for uri_node in part.children:
                                if uri_node.type == "uri":
                                    return uri_node.text.decode("utf-8").strip("'\"")
    return ""

def _extract_raw_import(node, language) -> str:
    """Given an import AST node, extract the module/path being imported.

    Different languages have different import structures in the AST.
    """
    if language == "python":
        for child in node.children:
            if child.type == "dotted_name":
                return child.text.decode("utf-8")
            if child.type == "relative_import":
                dotted = child.child_by_field_name("dotted_name")
                if dotted:
                    return dotted.text.decode("utf-8")

        return ""
    
    if language in ("javascript", "typescript"):
        # ES module: import_statement → string child
        if node.type == "import_statement":
            for child in node.children:
                if child.type == "string":
                    return child.text.decode("utf-8").strip("'\"")
            return ""
        
        # CommonJS: require('./path') — call_expression with "require" function
        if node.type == "call_expression":
            func = node.child_by_field_name("function")
            if func and func.text.decode("utf-8") == "require":
                args = node.child_by_field_name("arguments")
                if args:
                    for arg in args.children:
                        if arg.type == "string":
                            return arg.text.decode("utf-8").strip("'\"")
            return ""
        
        return ""
    
    if language == "dart":
        return _extract_dart_import(node)
    
    if language == "java":
        for child in node.children:
            if child.type == "scoped_identifier":
                return child.text.decode("utf-8")
        
        return ""
    
    if language == "go":
        for child in node.children:
            if child.type == "interpreted_string_literal":
                return child.text.decode("utf-8").strip('"')
            
        return ""
    
    if language in ("c", "cpp"):
        for child in node.children:
            if child.type in ("string_literal", "system_lib_string"):
                return child.text.decode("utf-8").strip('"<>')
        
        return ""
    
    if language == "rust":
        for child in node.children:
            if child.type in ("scoped_identifier", "identifier", "use_wildcard"):
                return child.text.decode("utf-8")
            
        return ""
    
    if language == "csharp":
        for child in node.children:
            if child.type in ("qualified_name", "identifier"):
                return child.text.decode("utf-8")
            
        return ""
    
    if language == "php":
        for child in node.children:
            if child.type == "namespace_use_clause":
                for c in child.children:
                    if c.type == "qualified_name":
                        return c.text.decode("utf-8")
        return ""
    
    if language == "ruby":
        if node.type == "call":
            text = node.text.decode("utf-8")
            if text.startswith("require"):
                for child in node.children:
                    if child.type == "argument_list":
                        for arg in child.children:
                            if arg.type == "string":
                                return arg.text.decode("utf-8").strip("'\"")
                            
        return ""
    
    if language == "kotlin":
      for child in node.children:
          if child.type == "qualified_identifier":
              return child.text.decode("utf-8")
      return ""

    if language == "swift":
      for child in node.children:
          if child.type == "identifier":
              return child.text.decode("utf-8")
      return ""
    
    return ""

def _resolve_java(raw_import: str, all_files: list[str]) -> str:
    """Resolve Java import by finding source root directories."""
    as_path = raw_import.replace(".", "/")
    suffix = f"{as_path}.java"

    if suffix in all_files:
        return suffix

    for f in all_files:
        if f.endswith(suffix):
            return f
        
    match = _suffix_match(as_path, [".java"], all_files)
    if match:
        return match

    return raw_import

def _suffix_match(as_path: str, extensions: list[str], all_files: list[str]) -> str:
    """Try progressively shorter suffixes of as_path against all_files.

    Handles the case where repo_path is a sub-directory:
      import src.codewalk.config → as_path = "src/codewalk/config"
      But all_files only has "config.py" (relative to src/codewalk/).

    Tries:
      src/codewalk/config.py → not found
      codewalk/config.py     → not found
      config.py              → FOUND ✓
    """
    parts = as_path.split("/")
    for i in range(1, len(parts)):
        suffix = "/".join(parts[i:])
        for ext in extensions:
            candidate = f"{suffix}{ext}"
            if candidate in all_files:
                return candidate
    return ""

def resolve_import_to_file(raw_import: str,language: str, all_files: list[str], source_file: str = "", dart_package: str = "") -> str:
    """Try to resolve a raw import string to an actual file in the repo.

    Returns the matching file path if found, otherwise returns the raw import.

    Args:
        raw_import: The raw import string from the AST.
        language: The language of the source file.
        all_files: List of all file paths in the repo (relative).
        source_file: The relative path of the file doing the importing
                     (needed for relative import resolution).
    """
    if language == "python":
        as_path = raw_import.replace(".", "/")
        candidates = [f"{as_path}.py", f"{as_path}/__init__.py"]
        for candidate in candidates:
            if candidate in all_files:
                return candidate
        match = _suffix_match(as_path, [".py", "/__init__.py"], all_files)
        if match:
            return match
            
    elif language in ("javascript", "typescript"):
        if raw_import.startswith("."):
            import posixpath
            source_dir = posixpath.dirname(source_file)
            resolved_base = posixpath.normpath(posixpath.join(source_dir, raw_import))

            # If import already has an extension, check as-is and with swapped extension
            if any(resolved_base.endswith(e) for e in (".ts", ".js", ".tsx", ".jsx", ".mjs", ".cjs")):
                if resolved_base in all_files:
                    return resolved_base
                # TS convention: import './foo.js' but file is foo.ts
                swaps = {".js": ".ts", ".jsx": ".tsx", ".mjs": ".mts", ".cjs": ".cts"}
                for old_ext, new_ext in swaps.items():
                    if resolved_base.endswith(old_ext):
                        swapped = resolved_base[:-len(old_ext)] + new_ext
                        if swapped in all_files:
                            return swapped
            else:
                # No extension — try adding each
                for ext in [".ts", ".js", ".tsx", ".jsx"]:
                    candidate = f"{resolved_base}{ext}"
                    if candidate in all_files:
                        return candidate
                # Try index file
                for ext in [".ts", ".js", ".tsx", ".jsx"]:
                    candidate = f"{resolved_base}/index{ext}"
                    if candidate in all_files:
                        return candidate
                
    elif language == "dart":
        if raw_import.startswith("dart:"):
            return raw_import
        # Self-referencing package import: package:<name>/x.dart → lib/x.dart
        if raw_import.startswith("package:") and dart_package:
            prefix = f"package:{dart_package}/"
            if raw_import.startswith(prefix):
                candidate = "lib/" + raw_import[len(prefix):]
                if candidate in all_files:
                    return candidate
            return raw_import
        if raw_import.startswith("package:"):
            return raw_import
        # Relative import — resolve relative to the source file's directory
        import posixpath
        source_dir = posixpath.dirname(source_file)
        candidate = posixpath.normpath(posixpath.join(source_dir, raw_import))
        if candidate in all_files:
            return candidate
            
    elif language == "java":
        return _resolve_java(raw_import, all_files)
            
    elif language == "go":
        parts = raw_import.strip("/").split("/")
        last_part = parts[-1] if parts else ""
        for file in all_files:
            if file.endswith(".go") and f"/{last_part}/" in f"/{file}":
                return file
            
    elif language == "rust":
        if raw_import.startswith("crate"):
            import posixpath
            # Find the crate root: look for Cargo.toml relative to source_file
            source_dir = posixpath.dirname(source_file)
            crate_root = ""
            parts = source_dir.split("/")
            for i in range(len(parts), 0, -1):
                prefix = "/".join(parts[:i])
                if f"{prefix}/Cargo.toml" in all_files:
                    crate_root = prefix
                    break

            crate_src = f"{crate_root}/src" if crate_root else "src"
            as_path = raw_import.replace("crate::", f"{crate_src}/").replace("::", "/")

            # Try exact path first
            candidates = [f"{as_path}.rs", f"{as_path}/mod.rs"]
            for candidate in candidates:
                if candidate in all_files:
                    return candidate

            # Strip last segment (item name) and try parent module
            parent = posixpath.dirname(as_path)
            if parent:
                candidates = [f"{parent}.rs", f"{parent}/mod.rs"]
                for candidate in candidates:
                    if candidate in all_files:
                        return candidate
                
    elif language == "ruby":
        if raw_import.startswith("."):
            base = raw_import.lstrip("./")
            candidate = f"{base}.rb"
            if candidate in all_files:
                return candidate
            
    elif language in ("c", "cpp"):
        if raw_import in all_files:
            return raw_import
        
        for prefix in ["include/", "src/"]:
            candidate = f"{prefix}{raw_import}"
            if candidate in all_files:
                return candidate
            
    elif language == "csharp":
        as_path = raw_import.replace(".", "/")
        candidate = f"{as_path}.cs"
        if candidate in all_files:
            return candidate
        
        match = _suffix_match(as_path, [".cs"], all_files)
        if match:
            return match
    
    elif language == "php":
        as_path = raw_import.replace("\\", "/")
        candidates = [f"{as_path}.php", f"src/{as_path}.php"]
        for candidate in candidates:
            if candidate in all_files:
                return candidate
        
        match = _suffix_match(as_path, [".php"], all_files)
        if match:
            return match
            
    elif language == "kotlin":
      # Kotlin imports are like Java: "okio.internal.Buffer" → "okio/internal/Buffer.kt"
      as_path = raw_import.replace(".", "/")
      suffix = f"{as_path}.kt"
      if suffix in all_files:
          return suffix
      for file in all_files:
          if file.endswith(suffix):
              return file
      # Suffix match for sub-directory repos
      match = _suffix_match(as_path, [".kt"], all_files)
      if match:
          return match

    elif language == "swift":
      # Swift imports are module-level (Foundation, UIKit)
      # No file-level imports in Swift — files in same module see each other automatically
      pass
            
    return raw_import

def _detect_dart_package_name(all_files: list[str], files: list[dict]) -> str:
    """Detect Dart package name from pubspec.yaml if present."""
    for file_info in files:
        if file_info["file_path"].endswith("pubspec.yaml"):
            try:
                content = Path(file_info["absolute_path"]).read_text()
                for line in content.split("\n"):
                    if line.startswith("name:"):
                        return line.split(":", 1)[1].strip()
            except (FileNotFoundError, PermissionError):
                pass
    return ""

def build_dependency_graph(files: list[dict]) -> dict:
    """Build a dependency graph from scanned files.

    Args:
        files: List of file dicts from scanner.scan_directory().
               Each dict has "file_path", "language", etc.

    Returns:
        {
            "graph": { "path/to/a.py": ["path/to/b.py", "os"], ... },
            "stats": { "total_files": N, "total_edges": N, "unresolved": N }
        }
    """
    # Step 1 — collect all file paths for resolution lookups
    all_file_paths = [file["file_path"] for file in files]

    # Dart-specific: detect package name from pubspec.yaml so we can resolve
    # self-referencing imports like "package:my_app/foo.dart" → "lib/foo.dart".
    # Without this, those imports stay unresolved (treated as external deps).
    # Harmless for non-Dart repos — only used inside the dart branch of
    # resolve_import_to_file(), ignored for all other languages.
    dart_package_name = _detect_dart_package_name(all_file_paths, files)

    graph = {}
    total_edges = 0
    unresolved_count = 0

    for file_info in files:
        file_path = file_info["file_path"]
        language = file_info["language"]

        # Step 2 — extract raw import strings using tree-sitter
        raw_imports = extract_imports(file_info["absolute_path"], language)

        resolved_imports = []

        # Step 3 — try to resolve each import to a real file
        for raw in raw_imports:
            resolved_path = resolve_import_to_file(raw, language, all_file_paths, source_file=file_path, dart_package=dart_package_name)
            resolved_imports.append(resolved_path)

            if resolved_path == raw:
                unresolved_count += 1
        
        graph[file_path] = resolved_imports
        total_edges += len(resolved_imports)

    _log(f"[dep_graph] Built graph: {len(files)} files, {total_edges} edges, {unresolved_count} unresolved")
    return {
        "graph": graph,
        "stats": {
            "total_files": len(files),
            "total_edges": total_edges,
            "unresolved":  unresolved_count,
        },
    }


    
