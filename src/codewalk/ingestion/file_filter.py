from pathlib import Path
import fnmatch as fnmatch_module

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
    # Framework build caches
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".turbo",
    ".nx",
    ".terraform",
    # Python tooling caches
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "htmlcov",
    ".eggs",
    "__pypackages__",
    ".hypothesis",
    # JS build / deploy
    ".cache",
    ".parcel-cache",
    "storybook-static",
    ".vercel",
    ".netlify",
    # Output / generated
    "out",
    "obj",
    "gen",
    "generated",
    "__generated__",
    "intermediates",
    # Xcode / Android NDK
    "xcuserdata",
    ".cxx",
    # Test / CI artifacts
    ".nyc_output",
    "test-results",
    "test-reports",
    # Docs build output
    "site",
    # Generic
    "tmp", "temp",
    "logs",
    "reports",
    "data",
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
    ".dex",
    ".beam",              # Erlang/Elixir
    ".hi",                # Haskell interface
    ".elc",               # Emacs Lisp
    ".rbc",               # Ruby compiled
    ".fasl",              # Common Lisp

    # App bundles / archives
    ".apk", ".ipa", ".aab",
    ".jar", ".aar",
    ".ear", ".war",

    # Images
    ".ico", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".bmp", ".tiff", ".webp", ".heic", ".heif",
    ".psd", ".ai", ".sketch", ".fig",

    # 3D / game assets
    ".fbx", ".glb", ".gltf", ".blend",
    ".unity", ".prefab",

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
    ".rst", ".txt", ".adoc",

    # Translation / localization data
    ".arb", ".xliff", ".xlf", ".po", ".pot", ".mo",
    ".strings", ".stringsdict",

    # Certificates / keys / secrets
    ".pem", ".crt", ".key", ".p12", ".pfx",
    ".secret", ".secrets", ".age",
    ".env.local", ".env.production",
    ".jks", ".keystore",  # Android signing
    ".cer", ".der", ".p8",
    ".mobileprovision",   # iOS provisioning

    # Terraform (state + vars may contain secrets)
    ".tfstate", ".tfvars",

    # Maps / generated
    ".map",
    ".ipynb",             # Jupyter — JSON blobs, can't chunk by function

    # Xcode / iOS generated
    ".pbxproj",           # Xcode project — huge auto-generated XML
    ".xcscheme",
    ".storyboard", ".xib",

    # GPU shaders
    ".glsl", ".hlsl", ".vert", ".frag",
    ".spv",               # SPIR-V compiled
    ".metal",             # Apple Metal

    # Debug symbols
    ".pdb",               # Windows debug
    ".res",               # Windows resource

    # Patch / diff
    ".patch", ".diff",

    # Temp / scratch / logs
    ".tmp", ".temp",
    ".bak", ".orig",
    ".log",
    ".cache",

    # Editor swap files
    ".swp", ".swo", ".swn",
    ".iml",

    # Profiling / dumps / coverage
    ".prof", ".cpuprofile",
    ".dmp", ".hprof",
    ".profdata", ".profraw",  # LLVM profiling
    ".lcov",                  # Coverage data
    ".gcda", ".gcno",         # GCC coverage instrumentation

    # Compiler-generated artifacts
    ".d",                     # GCC/Clang dependency files
    ".pch", ".gch",           # Precompiled headers
    ".rlib",                  # Rust compiled library
    ".jmod",                  # Java module format

    # Unity-specific
    ".meta",                  # Auto-generated for every asset
    ".anim",                  # Animation clip
    ".controller",            # Animator controller
    ".lighting",              # Baked lighting
    ".shadergraph",           # Shader graph asset

    # Visual Studio binary
    ".suo",                   # Solution user options
    ".sdf",                   # IntelliSense DB
    ".ncb",                   # Old VS IntelliSense DB
    ".user",                  # Per-user project settings

    # R / MATLAB data
    ".rda", ".rds", ".rdata",
    ".mat",                   # MATLAB data

    # Ops / infra
    ".tfplan",                # Terraform plan output
    ".retry",                 # Ansible retry files
    ".webmanifest",           # Web app manifest

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
    "poetry.lock",
    "uv.lock",
    "mix.lock",
    "stack.yaml.lock",
    "deno.lock",
    "npm-shrinkwrap.json",
    ".terraform.lock.hcl",
    # OS junk
    "Thumbs.db",
    "desktop.ini",
    # Python distribution
    "MANIFEST",
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
    # Protobuf / gRPC generated
    ".pb.swift",
    ".pb.dart",
    "_pb2_grpc.py",
    ".grpc.swift",
    # GraphQL codegen
    ".graphql.ts",
    ".gql.ts",
    # Go generated
    "_generated.go",
    "_mock.go",
    ".mock.go",
    # JS/TS generated
    ".generated.ts",
    ".generated.js",
    ".chunk.css",
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
    
    # Check .codewalkignore patterns
    if _codewalkignore_matches(file_path):
        return True

    return False


# ─── .codewalkignore support ─────────────────────────────────────────

_codewalkignore_patterns: list[str] | None = None

def _load_codewalkignore() -> list[str]:
    """Load patterns from .codewalkignore in the repo root (gitignore syntax)."""
    global _codewalkignore_patterns
    if _codewalkignore_patterns is not None:
        return _codewalkignore_patterns

    from src.codewalk.config import settings
    ignore_path = Path(settings.repo_path) / ".codewalkignore"
    if not ignore_path.exists():
        _codewalkignore_patterns = []
        return _codewalkignore_patterns

    patterns = []
    for line in ignore_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    _codewalkignore_patterns = patterns
    return _codewalkignore_patterns


def _codewalkignore_matches(file_path: str) -> bool:
    """Check if a file path matches any .codewalkignore pattern."""
    patterns = _load_codewalkignore()
    if not patterns:
        return False

    for pattern in patterns:
        # Directory pattern (ends with /)
        if pattern.endswith("/"):
            dir_name = pattern.rstrip("/")
            if dir_name in Path(file_path).parts:
                return True
        # Glob pattern
        elif fnmatch_module.fnmatch(file_path, pattern):
            return True
        # Also check just the filename
        elif fnmatch_module.fnmatch(Path(file_path).name, pattern):
            return True
        # Check if pattern matches any path segment
        elif "/" not in pattern and pattern in Path(file_path).parts:
            return True
    return False


def reset_codewalkignore():
    """Reset cached patterns (call when repo_path changes)."""
    global _codewalkignore_patterns
    _codewalkignore_patterns = None
        