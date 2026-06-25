"""File and directory skip rules for repo scanning."""
from pathlib import Path
import fnmatch as fnmatch_module

# Dot-folders to KEEP (useful code/config)
KEEP_DOT_DIRS = {
    ".github",
}

# Core safety-net directories that are always pruned.
# These are universally dangerous/useless to index: version-control metadata,
# dependency folders, build/cache output, and generated artifacts.
# Repo- or framework-specific exclusions (tools/, scripts/, cdk/, migrations/,
# story files, etc.) belong in codewalk.yaml, not here.
CORE_SKIP_DIRS = {
    # Version control / Codewalk internal
    ".git",
    ".codewalk",
    # JS/TS dependencies / build
    "node_modules",
    "bower_components",
    # Python environments / caches
    "__pycache__",
    "venv",
    ".venv",
    "env",
    ".env",
    "egg-info",
    ".codewalk-env",
    # Generic build / output
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
    # Go / Ruby / PHP dependencies
    "vendor",
    "deps",
    # Swift
    "Packages",
    # Elixir
    "_build",
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
    # JS build / deploy caches
    ".cache",
    ".parcel-cache",
    "storybook-static",
    ".vercel",
    ".netlify",
    # Output / generated directories
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
    # Generic temp / logs
    "tmp",
    "temp",
    "logs",
    "reports",
}

# Backwards-compatible aliases used by tests and existing callers.
SKIP_DIRS = CORE_SKIP_DIRS

# Core safety-net extensions that are never useful code:
# binaries, media, archives, fonts, lock files, secrets, generated artifacts.
# Keep this list conservative; stack-specific patterns belong in codewalk.yaml.
CORE_SKIP_EXTENSIONS = {
    # Compiled / bytecode
    ".pyc", ".pyo", ".pyd",
    ".class",
    ".o", ".obj", ".a", ".lib",
    ".so", ".dylib", ".dll",
    ".exe",
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

# Backwards-compatible alias.
SKIP_EXTENSIONS = CORE_SKIP_EXTENSIONS

# Specific filenames to skip (generated / lock files / OS junk).
CORE_SKIP_FILES = {
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

# Backwards-compatible alias.
SKIP_FILES = CORE_SKIP_FILES

# Filename suffixes for generated/auto-generated code.
CORE_SKIP_SUFFIXES = (
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

# Backwards-compatible alias.
SKIP_SUFFIXES = CORE_SKIP_SUFFIXES


def should_skip_dir(dir_name: str) -> bool:
    """Return True if this directory should be pruned during os.walk.

    This is the core safety net only. Framework- and repo-specific directory
    pruning should be configured via codewalk.yaml indexing.exclude.
    """
    if dir_name.startswith(".") and dir_name not in KEEP_DOT_DIRS:
        return True
    if dir_name in CORE_SKIP_DIRS:
        return True
    return False


def should_skip(file_path: str, repo_path: str | None = None) -> bool:
    """Return True if this file should be skipped by the core safety net."""

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
        if part in CORE_SKIP_DIRS:
            return True

    # Skip binary/media extensions
    if path.suffix in CORE_SKIP_EXTENSIONS:
        return True

    # Skip specific filenames
    if path.name in CORE_SKIP_FILES:
        return True

    # Skip generated file patterns (e.g. foo.g.dart, bar.pb.go, baz.designer.cs)
    if any(path.name.endswith(suffix) for suffix in CORE_SKIP_SUFFIXES):
        return True

    # Check .codewalkignore patterns
    if _codewalkignore_matches(file_path, repo_path=repo_path):
        return True

    return False


# ─── .codewalkignore support ─────────────────────────────────────────

_codewalkignore_patterns: dict[str, list[str]] = {}


def _load_codewalkignore(repo_path: str | None = None) -> list[str]:
    """Load patterns from .codewalkignore in the repo root (gitignore syntax)."""
    global _codewalkignore_patterns

    root = (repo_path or ".").strip()
    if root in _codewalkignore_patterns:
        return _codewalkignore_patterns[root]

    ignore_path = Path(root) / ".codewalkignore"
    if not ignore_path.exists():
        _codewalkignore_patterns[root] = []
        return []

    patterns = []
    for line in ignore_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    _codewalkignore_patterns[root] = patterns
    return patterns


def _codewalkignore_matches(file_path: str, repo_path: str | None = None) -> bool:
    """Check if a file path matches any .codewalkignore pattern."""
    patterns = _load_codewalkignore(repo_path)
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
    _codewalkignore_patterns = {}
