from pathlib import Path

# Dot-folders to KEEP (useful code/config)
KEEP_DOT_DIRS = {
    ".github",
}

# Non-dot directories to skip
SKIP_DIRS = {
    # JS/TS
    "node_modules",
    "bower_components",
    # Python
    "__pycache__",
    "venv",
    ".venv",
    "env",
    ".env",
    "egg-info",
    ".codewalk-env",
    # Generic build
    "dist",
    "build",
    "target",
    "coverage",
    # iOS / macOS
    "Pods",
    "DerivedData",
    "Carthage",
    # Flutter / Dart
    "ephemeral",
    ".dart_tool",
    # Go / Ruby / PHP
    "vendor",
    "deps",
    # Swift
    "Packages",
    # Elixir
    "_build",
    # Testing / fixtures
    "__tests__",
    "__snapshots__",
    "testdata",
    "fixtures",
    # Localization
    "l10n",
    "locales",
    "i18n",
    # Migrations
    "migrations",
    # Gradle
    ".gradle",
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
    ".md", ".rst", ".txt", ".adoc",

    # Translation / localization data
    ".arb", ".xliff", ".xlf", ".po", ".pot", ".mo",
    ".strings", ".stringsdict",

    # Certifica / keys
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

# Filename suffixes for generated/auto-generated code
SKIP_SUFFIXES = (
    ".g.dart",
    ".freezed.dart",
    ".gen.dart",
    ".generated.dart",
    ".g.cs",
    ".designer.cs",
    ".pb.go",
    "_pb2.py",
    ".min.js",
    ".min.css",
    ".bundle.js",
    ".chunk.js",
)

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
    
    # Skip generated file patterns (e.g. foo.g.dart, bar.freezed.dart)
    if any(path.name.endswith(suffix) for suffix in SKIP_SUFFIXES):
        return True
    
    return False
        