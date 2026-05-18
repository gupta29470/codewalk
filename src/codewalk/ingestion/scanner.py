"""
=============================================================================
 scanner.py — Directory Scanner (File Discovery)
=============================================================================

WHAT THIS FILE DOES:
    Walks through a directory tree and builds a list of ALL files found,
    along with metadata about each file (path, language, size).
    
    It's the FIRST step in the pipeline — before you can chunk, embed, or
    analyze code, you need to FIND all the files.

HOW IT WORKS:
    1. Takes a directory path (e.g. "/Users/dev/Konnect/lib")
    2. Recursively walks through every file using Path.rglob("*")
    3. For each file:
       a. Computes relative path (for display/storage)
       b. Checks should_skip() — skips node_modules, __pycache__, etc.
       c. Detects language from file extension (.dart → "dart")
       d. Gets file size
    4. Returns list of dicts: [{file_path, absolute_path, language, size_bytes}]

REAL-WORLD ANALOGY:
    Like doing an inventory of a warehouse. You walk through every aisle
    (directory), note what's on each shelf (file), what type it is
    (language), and how big it is (size). Skip the dumpster (node_modules).

WHERE IT'S CALLED:
    - codewalk_analyze_codebase() in server.py → "scan all files in the repo"
    - codewalk_scan_files() in server.py → scanning for the filter workflow
    - pipeline.py → during full indexing

DEPENDENCIES:
    - file_filter.py: provides should_skip() — the filtering logic
    - log.py: for logging how many files were found

=============================================================================
"""

# ─── Imports ─────────────────────────────────────────────────────────

import logging
from pathlib import Path  # Object-oriented filesystem paths

# should_skip() returns True for files we don't want to index
# (node_modules, .pyc files, images, lock files, etc.)
from src.codewalk.ingestion.file_filter import should_skip
from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")


# =============================================================================
# EXTENSION_MAP — File Extension → Language Name
# =============================================================================
#
# HOW TO READ THIS:
#   ".dart": "dart" means → if a file ends in .dart, its language is "dart"
#
# WHY WE NEED THIS:
#   When we chunk code later, we need to know the language to pick the right
#   parser (Python AST for .py, tree-sitter for .dart, etc.)
#   The language also gets stored as metadata in ChromaDB embeddings.
#
# NOTE: Both .jsx and .tsx map to their base languages because the chunker
#       treats them the same way.

EXTENSION_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",   # React JSX = still JavaScript
    ".tsx": "typescript",   # React TSX = still TypeScript
    ".dart": "dart",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".c": "c",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",         # .yml and .yaml are the same format
    ".toml": "toml",
    ".txt": "text",
    ".kt": "kotlin",
    ".swift": "swift",
}


# =============================================================================
# detect_language() — Extension → Language Lookup
# =============================================================================

def detect_language(file_path: Path) -> str:
    """Look at the file extension and return the language name.

    EXECUTION FLOW:
        file_path = Path("lib/src/features/home/home_bloc.dart")
        file_path.suffix = ".dart"
        file_path.suffix.lower() = ".dart"
        EXTENSION_MAP.get(".dart", "unknown") → "dart"

    Args:
        file_path: A Path object for any file.

    Returns:
        Language string like "dart", "python", "typescript".
        Returns "unknown" if extension isn't in the map.
    """
    return EXTENSION_MAP.get(file_path.suffix.lower(), "unknown")


# =============================================================================
# scan_directory() — The Main Function
# =============================================================================

def scan_directory(directory: str) -> list[dict]:
    """Walk a directory recursively and return info about every file.

    EXECUTION FLOW (example with a Flutter repo):
        1. directory = "/Users/dev/Konnect"
        2. root = Path("/Users/dev/Konnect")
        3. root.rglob("*") yields:
             /Users/dev/Konnect/lib/main.dart
             /Users/dev/Konnect/lib/src/features/home/home_bloc.dart
             /Users/dev/Konnect/node_modules/react/index.js  ← will be skipped
             /Users/dev/Konnect/assets/logo.png              ← will be skipped
             ...
        4. For each file:
             relative = "lib/src/features/home/home_bloc.dart"
             should_skip("lib/src/features/home/home_bloc.dart") → False → KEEP
             language = "dart"
             size = 2048 bytes
        5. Returns [{
             "file_path": "lib/src/features/home/home_bloc.dart",
             "absolute_path": "/Users/dev/Konnect/lib/src/features/home/home_bloc.dart",
             "language": "dart",
             "size_bytes": 2048
           }, ...]

    Args:
        directory: Absolute path to scan.

    Returns:
        List of file info dicts. Each dict has:
        - file_path: relative path (used as identifier throughout the system)
        - absolute_path: full path (for reading file contents)
        - language: detected from extension
        - size_bytes: file size on disk
    """
    root = Path(directory)

    if not root.exists():
        raise FileNotFoundError(f"Directory {directory} does not exist.")
    
    files = []

    # rglob("*") = recursive glob → walks ALL subdirectories
    # Yields every file and folder. We filter to files only.
    for file_path in root.rglob("*"):
        if not file_path.is_file():  # Skip directories themselves
            continue

        # Relative path: remove the root prefix
        # /Users/dev/Konnect/lib/main.dart → "lib/main.dart"
        relative = str(file_path.relative_to(root))

        # Ask file_filter.py: should we skip this?
        # Skips: node_modules/, __pycache__/, .png, .lock, etc.
        if should_skip(relative):
            continue

        files.append({
            "file_path": relative,              # Used as ID everywhere
            "absolute_path": str(file_path),    # For actually reading the file
            "language": detect_language(file_path),  # From extension
            "size_bytes": file_path.stat().st_size,  # os.stat() → file size
        })
    
    _log(f"[scanner] Scanned {directory} → {len(files)} files")
    return files
