"""
=============================================================================
 dependency_graph.py — Import Extraction & File-Level Dependency Graph
=============================================================================

WHAT THIS FILE DOES:
    Builds a dependency graph showing which files import which other files.
    For EVERY source file in the repo, it:
      1. Parses the file with tree-sitter to extract import statements
      2. Resolves each import string to an actual file path in the repo
      3. Produces: {"auth.py": ["db.py", "config.py"], "db.py": ["config.py"]}

HOW IT WORKS (THREE STAGES):

    STAGE 1 — Extract Raw Imports (tree-sitter)
        Each language has different import syntax in the AST:
          Python: import_statement, import_from_statement → "os", "src.codewalk.config"
          JS/TS: import_statement → "./auth_service", "express"
          Dart: import_or_export → "package:flutter/material.dart"
          Java: import_declaration → "com.example.service.AuthService"

    STAGE 2 — Resolve Imports to File Paths
        Raw import strings don't match file paths directly:
          "src.codewalk.config" → "src/codewalk/config.py"  (Python: dots → slashes)
          "./auth_service" → "auth_service.ts"  (JS: add extension, resolve relative)
          "package:my_app/models/user.dart" → "lib/models/user.dart"  (Dart: package → lib/)

    STAGE 3 — Build Graph Dict
        Result: {"file_a.py": ["file_b.py", "file_c.py"], ...}
        If an import can't be resolved (external package), it stays as raw text.

REAL-WORLD ANALOGY:
    Like building a "who calls whom" phone directory. For each person (file),
    list everyone they call (import). This lets you answer:
    "If I change config.py, who gets affected?" → everyone who imports it.

WHERE IT'S CALLED:
    - pipeline.py → build_dependency_graph() during analysis phase
    - module_detector.py → uses the graph to build module-level dependencies
    - reading_order.py → uses the graph for topological sort

DEPENDENCIES:
    - code_parser.py: tree-sitter grammar loading (GRAMMAR_MAP, get_parser_for_language)
    - tree-sitter: actual AST parsing

=============================================================================
"""

# ─── Imports ─────────────────────────────────────────────────────────

import logging
from pathlib import Path

from src.codewalk.log import log as _log
from src.codewalk.analysis.code_parser import (
    get_parser_for_language,
    GRAMMAR_MAP
)

logger = logging.getLogger("codewalk")


# =============================================================================
# IMPORT_NODE_TYPES — Which AST Nodes Represent Imports Per Language
# =============================================================================
# Each language has different node types for import/require statements.
# This tells the tree walker what to look for.

IMPORT_NODE_TYPES = {
    "python":     ["import_statement", "import_from_statement"],
    "javascript": ["import_statement"],
    "typescript": ["import_statement"],
    "dart":       ["import_or_export"],
    "java":       ["import_declaration"],
    "go":         ["import_declaration", "import_spec"],
    "rust":       ["use_declaration"],
    "ruby":       ["call"],       # require() / require_relative() are function calls
    "php":        ["namespace_use_declaration"],
    "c":          ["preproc_include"],    # #include "header.h"
    "cpp":        ["preproc_include"],
    "csharp":     ["using_directive"],
    "kotlin":     ["import"],
    "swift":      ["import_declaration"],
}


# =============================================================================
# extract_imports() — Stage 1: Get Raw Import Strings From a File
# =============================================================================

def extract_imports(file_path: str, language: str) -> list[str]:
    """Parse a file with tree-sitter and extract all import strings.

    RETURNS raw import strings (not yet resolved to file paths):
        Python:  ["os", "pathlib.Path", "src.codewalk.config"]
        JS/TS:   ["express", "./auth_service"]
        Dart:    ["package:flutter/material.dart", "../models/user.dart"]
        Java:    ["com.example.service.AuthService"]
        Go:      ["fmt", "github.com/gin-gonic/gin"]
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
    """Walk AST and yield import nodes. Simple recursive traversal."""
    if node.type in target_types:
        yield node
    for child in node.children:
        yield from _walk_for_imports(child, target_types)


# =============================================================================
# _extract_raw_import() — Language-Specific Import Text Extraction
# =============================================================================

def _extract_dart_import(node) -> str:
    """Navigate Dart's nested AST to find the import URI string.

    Dart import AST structure:
        import_or_export → library_import → import_specification → configurable_uri → uri
    We drill down through each level to reach the actual string.
    """
    for child in node.children:
        if child.type == "library_import":
            for spec in child.children:
                if spec.type == "import_specification":
                    for part in spec.children:
                        if part.type == "configurable_uri":
                            for uri_node in part.children:
                                if uri_node.type == "uri":
                                    return uri_node.text.decode("utf-8").strip("\'"\""\")
    return ""


def _extract_raw_import(node, language) -> str:
    """Given an import AST node, extract the module/path being imported.

    Each language stores the import target in a different child node type.
    This function handles all 14 supported languages.
    """
    if language == "python":
        # Python: "from pathlib import Path" → node has dotted_name child "pathlib"
        #         "import os" → node has dotted_name child "os"
        for child in node.children:
            if child.type == "dotted_name":
                return child.text.decode("utf-8")
            if child.type == "relative_import":
                dotted = child.child_by_field_name("dotted_name")
                if dotted:
                    return dotted.text.decode("utf-8")
        return ""

    if language in ("javascript", "typescript"):
        # JS/TS: import x from "module-path" → string node contains the path
        for child in node.children:
            if child.type == "string":
                return child.text.decode("utf-8").strip("\'"\""\")
        return ""

    if language == "dart":
        return _extract_dart_import(node)

    if language == "java":
        # Java: import com.example.Service → scoped_identifier
        for child in node.children:
            if child.type == "scoped_identifier":
                return child.text.decode("utf-8")
        return ""

    if language == "go":
        # Go: import "github.com/gin-gonic/gin" → interpreted_string_literal
        for child in node.children:
            if child.type == "interpreted_string_literal":
                return child.text.decode("utf-8").strip(\'"\')
        return ""

    if language in ("c", "cpp"):
        # C/C++: #include "header.h" or #include <stdio.h>
        for child in node.children:
            if child.type in ("string_literal", "system_lib_string"):
                return child.text.decode("utf-8").strip(\'"<>\')
        return ""

    if language == "rust":
        # Rust: use std::collections::HashMap → scoped_identifier
        for child in node.children:
            if child.type in ("scoped_identifier", "identifier", "use_wildcard"):
                return child.text.decode("utf-8")
        return ""

    if language == "csharp":
        # C#: using System.Collections.Generic → qualified_name
        for child in node.children:
            if child.type in ("qualified_name", "identifier"):
                return child.text.decode("utf-8")
        return ""

    if language == "php":
        # PHP: use App\Models\User → namespace_use_clause → qualified_name
        for child in node.children:
            if child.type == "namespace_use_clause":
                for c in child.children:
                    if c.type == "qualified_name":
                        return c.text.decode("utf-8")
        return ""

    if language == "ruby":
        # Ruby: require "json" or require_relative "./helper"
        if node.type == "call":
            text = node.text.decode("utf-8")
            if text.startswith("require"):
                for child in node.children:
                    if child.type == "argument_list":
                        for arg in child.children:
                            if arg.type == "string":
                                return arg.text.decode("utf-8").strip("\'"\""\")
        return ""

    if language == "kotlin":
        # Kotlin: import okio.internal.Buffer → qualified_identifier
        for child in node.children:
            if child.type == "qualified_identifier":
                return child.text.decode("utf-8")
        return ""

    if language == "swift":
        # Swift: import Foundation → identifier (module-level only)
        for child in node.children:
            if child.type == "identifier":
                return child.text.decode("utf-8")
        return ""

    return ""


# =============================================================================
# Stage 2: Import Resolution — Raw String → File Path
# =============================================================================
# Each language has different conventions for how imports map to files.

def _resolve_java(raw_import: str, all_files: list[str]) -> str:
    """Resolve Java import: com.example.Service → find Service.java in the repo."""
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

    HANDLES NESTED REPOS:
        import src.codewalk.config → as_path = "src/codewalk/config"
        But all_files might only have "config.py" (relative to a sub-directory)

    TRIES (from longest to shortest):
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


def resolve_import_to_file(raw_import: str, language: str, all_files: list[str],
                           source_file: str = "", dart_package: str = "") -> str:
    """Resolve a raw import string to an actual file path in the repo.

    LANGUAGE-SPECIFIC RESOLUTION STRATEGIES:

    PYTHON: dots → slashes, try .py and /__init__.py
        "src.codewalk.config" → "src/codewalk/config.py"
        "src.codewalk.config" → "src/codewalk/config/__init__.py"

    JS/TS: relative paths + extension guessing
        "./auth_service" → try auth_service.ts, .js, .tsx, .jsx
        "./auth_service" → try auth_service/index.ts, /index.js

    DART: package: prefix → lib/ folder
        "package:my_app/models/user.dart" → "lib/models/user.dart"
        "../widgets/button.dart" → resolve relative to source file

    JAVA: dots → slashes + .java
        "com.example.Service" → find "**/Service.java"

    Returns the resolved file path if found, otherwise the raw import string.
    (Unresolved imports are treated as external dependencies.)
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

            # Import already has extension → check as-is and with TS↔JS swap
            if any(resolved_base.endswith(e) for e in (".ts", ".js", ".tsx", ".jsx", ".mjs", ".cjs")):
                if resolved_base in all_files:
                    return resolved_base
                # TS convention: import './foo.js' but actual file is foo.ts
                swaps = {".js": ".ts", ".jsx": ".tsx", ".mjs": ".mts", ".cjs": ".cts"}
                for old_ext, new_ext in swaps.items():
                    if resolved_base.endswith(old_ext):
                        swapped = resolved_base[:-len(old_ext)] + new_ext
                        if swapped in all_files:
                            return swapped
            else:
                # No extension → try common extensions
                for ext in [".ts", ".js", ".tsx", ".jsx"]:
                    candidate = f"{resolved_base}{ext}"
                    if candidate in all_files:
                        return candidate
                # Try index file (import "./components" → "./components/index.ts")
                for ext in [".ts", ".js", ".tsx", ".jsx"]:
                    candidate = f"{resolved_base}/index{ext}"
                    if candidate in all_files:
                        return candidate

    elif language == "dart":
        if raw_import.startswith("dart:"):
            return raw_import  # SDK import — external
        # Self-referencing package import: package:<name>/x.dart → lib/x.dart
        if raw_import.startswith("package:") and dart_package:
            prefix = f"package:{dart_package}/"
            if raw_import.startswith(prefix):
                candidate = "lib/" + raw_import[len(prefix):]
                if candidate in all_files:
                    return candidate
            return raw_import  # Other packages — external
        if raw_import.startswith("package:"):
            return raw_import  # External package
        # Relative import
        import posixpath
        source_dir = posixpath.dirname(source_file)
        candidate = posixpath.normpath(posixpath.join(source_dir, raw_import))
        if candidate in all_files:
            return candidate

    elif language == "java":
        return _resolve_java(raw_import, all_files)

    elif language == "go":
        # Go: match last path segment as a directory containing .go files
        parts = raw_import.strip("/").split("/")
        last_part = parts[-1] if parts else ""
        for file in all_files:
            if file.endswith(".go") and f"/{last_part}/" in f"/{file}":
                return file

    elif language == "rust":
        if raw_import.startswith("crate"):
            import posixpath
            # Find crate root by locating Cargo.toml
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

            candidates = [f"{as_path}.rs", f"{as_path}/mod.rs"]
            for candidate in candidates:
                if candidate in all_files:
                    return candidate

            # Strip last segment (item name) → try parent module
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
        as_path = raw_import.replace(".", "/")
        suffix = f"{as_path}.kt"
        if suffix in all_files:
            return suffix
        for file in all_files:
            if file.endswith(suffix):
                return file
        match = _suffix_match(as_path, [".kt"], all_files)
        if match:
            return match

    elif language == "swift":
        # Swift imports are module-level — files in same module see each other
        pass

    return raw_import  # Unresolved → treated as external dependency


# =============================================================================
# Dart Helper
# =============================================================================

def _detect_dart_package_name(all_files: list[str], files: list[dict]) -> str:
    """Detect Dart package name from pubspec.yaml if present.

    Needed for resolving self-referencing imports:
        "package:my_app/models/user.dart" → need to know "my_app" is THIS package
        then resolve to: "lib/models/user.dart"
    """
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


# =============================================================================
# build_dependency_graph() — Stage 3: The Main Entry Point
# =============================================================================

def build_dependency_graph(files: list[dict]) -> dict:
    """Build file-level dependency graph from all scanned files.

    EXECUTION FLOW:
        1. Collect all file paths (for resolution lookups)
        2. Detect Dart package name (if applicable)
        3. For each file:
           a. extract_imports() → raw strings via tree-sitter
           b. resolve_import_to_file() → map to actual paths
        4. Return graph + stats

    RETURNS:
        {
            "graph": {
                "src/codewalk/config.py": [],
                "src/codewalk/scanner.py": ["src/codewalk/config.py", "os"],
                "src/codewalk/pipeline.py": ["src/codewalk/scanner.py", "src/codewalk/config.py"],
            },
            "stats": {
                "total_files": 20,
                "total_edges": 45,    ← total import relationships
                "unresolved": 12      ← imports that couldn't be mapped to files (external packages)
            }
        }
    """
    all_file_paths = [file["file_path"] for file in files]
    dart_package_name = _detect_dart_package_name(all_file_paths, files)

    graph = {}
    total_edges = 0
    unresolved_count = 0

    for file_info in files:
        file_path = file_info["file_path"]
        language = file_info["language"]

        # Extract raw import strings using tree-sitter
        raw_imports = extract_imports(file_info["absolute_path"], language)

        resolved_imports = []

        # Resolve each import to a file path
        for raw in raw_imports:
            resolved_path = resolve_import_to_file(
                raw, language, all_file_paths,
                source_file=file_path,
                dart_package=dart_package_name
            )
            resolved_imports.append(resolved_path)

            # If resolution returned the raw string unchanged → unresolved
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
            "unresolved": unresolved_count,
        },
    }
