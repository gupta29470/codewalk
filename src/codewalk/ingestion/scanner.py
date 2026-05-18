import logging
from pathlib import Path

from src.codewalk.ingestion.file_filter import should_skip
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
    ".c": "c",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".txt": "text",
    ".kt": "kotlin",
    ".swift": "swift",
}

def detect_language(file_path: Path) -> str:
    """Look at the file extension → return the language name."""
    return EXTENSION_MAP.get(file_path.suffix.lower(), "unknown")


def scan_directory(directory: str) -> list[dict]:
    """Walk a directory → return info about every file."""
    root = Path(directory)

    if not root.exists():
        raise FileNotFoundError(f"Directory {directory} does not exist.")
    
    files = []

    for file_path in root.rglob("*"):
        if not file_path.is_file():
            continue

        relative = str(file_path.relative_to(root))

        if should_skip(relative):
            continue

        files.append({
            "file_path": relative,
            "absolute_path": str(file_path),
            "language": detect_language(file_path),
            "size_bytes": file_path.stat().st_size,
        })
    
    _log(f"[scanner] Scanned {directory} → {len(files)} files")
    return files