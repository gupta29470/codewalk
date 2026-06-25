"""Directory scanning and file enumeration for indexing."""
import logging
import os as _os
from pathlib import Path

from src.codewalk.ingestion.file_filter import should_skip, should_skip_dir
from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")

# Map file extensions to language names
EXTENSION_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".dart": "dart",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".c": "c",
    ".h": "c",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".txt": "text",
    ".kt": "kotlin",
    ".swift": "swift",
    ".m": "objc",
    ".mm": "objc",
    ".sql": "sql",
    ".proto": "protobuf",
    ".graphql": "graphql",
    ".gql": "graphql",
}

def detect_language(file_path: Path) -> str:
    """Look at the file extension → return the language name."""
    return EXTENSION_MAP.get(file_path.suffix.lower(), "unknown")


def scan_directory(directory: str) -> list[dict]:
    """Walk a directory → return info about every file.
    Prunes excluded directories early via os.walk to skip entire subtrees.
    """
    root = Path(directory)

    if not root.exists():
        raise FileNotFoundError(f"Directory {directory} does not exist.")
    
    files = []
    root_str = str(root)

    for dirpath, dirs, filenames in _os.walk(root):
        # Step 1: Prune excluded dirs IN-PLACE — os.walk won't descend into them
        dirs[:] = [d for d in dirs if not should_skip_dir(d)]

        rel_dir = _os.path.relpath(dirpath, root_str)

        # Step 2: Filter files from remaining directories
        for fname in filenames:
            relative = _os.path.join(rel_dir, fname) if rel_dir != "." else fname

            if should_skip(relative, repo_path=root_str):
                continue

            full_path = _os.path.join(dirpath, fname)
            files.append({
                "file_path": relative,
                "absolute_path": full_path,
                "language": detect_language(Path(full_path)),
                "size_bytes": _os.path.getsize(full_path),
            })
    
    _log(f"[scanner] Scanned {directory} → {len(files)} files")
    return files