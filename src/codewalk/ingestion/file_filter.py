from pathlib import Path

# Dot-folders to KEEP (useful code/config)
KEEP_DOT_DIRS = {
    ".github",
}

# Non-dot directories to skip
SKIP_DIRS = {
    "node_modules",
    "__pycache__",
    "dist",
    "build",
    "venv",
    "egg-info",
}

# Extensions that aren't code (binary/media files)
# Extensions that aren't useful code (binary, media, lock files, generated)
SKIP_EXTENSIONS = {
    # Compiled / bytecode
    ".pyc", ".pyo", ".pyd",
    ".class",
    ".o", ".obj", ".a", ".lib",
    ".so", ".dylib", ".dll",
    ".exe", ".bin",
    ".wasm",

    # Images
    ".ico", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".bmp", ".tiff", ".webp", ".heic", ".heif",
    ".psd", ".ai", ".sketch", ".fig",

    # Fonts
    ".woff", ".woff2", ".ttf", ".eot", ".otf",

    # Audio / Video
    ".mp3", ".wav", ".ogg", ".flac", ".aac",
    ".mp4", ".avi", ".mov", ".mkv", ".webm",

    # Archives
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".rar", ".7z",
    ".tgz",

    # Lock files (auto-generated, not hand-written)
    ".lock",

    # Database / data files
    ".db", ".sqlite", ".sqlite3",
    ".h5", ".hdf5", ".pkl", ".pickle",

    # ML model files
    ".pt", ".pth", ".onnx", ".safetensors",
    ".bin", ".model", ".weights",

    # Documents (not code)
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",

    # Certificates / keys
    ".pem", ".crt", ".key", ".p12", ".pfx",

    # Maps / generated
    ".map",

    # Misc binary
    ".dat", ".data", ".npy", ".npz",
    ".parquet", ".feather", ".arrow",
    ".tfrecord",
    ".coverage",
}

# Specific filenames to skip (generated / lock files)
SKIP_FILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "pubspec.lock",
    "Podfile.lock",
    "Gemfile.lock",
    "composer.lock",
    "Cargo.lock",
    "go.sum",
    "flake.lock",
    "bun.lockb",
}

def should_skip(file_path: str) -> bool:
    """Return True if this file should be skipped."""

    path = Path(file_path)

    # Skip hidden directories (starting with .) EXCEPT whitelisted ones
    for part in path.parts[:-1]:
        if part.startswith(".") and part not in KEEP_DOT_DIRS:
            return True
    
    # Skip hidden files (starting with .)
    if path.name.startswith("."):
        return True

    # Skip junk directories
    for part in path.parts:
        if part in SKIP_DIRS:
            return True
        
    
    # Skip binary/media extensions
    if path.suffix in SKIP_EXTENSIONS:
        return True
    
    # Skip specific filenames
    if path.name in SKIP_FILES:
        return True
    
    return False
        