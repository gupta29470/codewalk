"""
=============================================================================
 file_filter.py — Decide Which Files to Index (Skip Junk)
=============================================================================

WHAT THIS FILE DOES:
    When scanning a repository, NOT every file should be indexed.
    This module decides which files to SKIP:
      - Binary files (images, videos, compiled code)
      - Generated code (*.g.dart, *.min.js, protobuf output)
      - Lock files (package-lock.json, yarn.lock)
      - Build artifacts (dist/, node_modules/, __pycache__/)
      - Non-code files (PDFs, fonts, certificates)

    After filtering, only actual hand-written source code remains.

HOW IT WORKS:
    should_skip(file_path) checks 6 rules in order:
      1. Hidden dot-folders (skip .git, keep .github)
      2. Hidden dot-files (skip .DS_Store, .env)
      3. Junk directories (skip node_modules/, __pycache__/)
      4. Binary/media extensions (skip .png, .exe, .woff)
      5. Specific filenames (skip package-lock.json)
      6. Generated code suffixes (skip *.g.dart, *.min.js)
      7. .codewalkignore patterns (user-defined skip rules)

REAL-WORLD ANALOGY:
    Like a librarian deciding which documents go into the searchable catalog.
    You index the books (source code), but NOT the packaging material
    (node_modules), inventory receipts (lock files), or photographs (images).

WHY SO MANY SKIP RULES?
    A typical project has 80% non-code files by count:
      React app: 40,000 files → 38,000 in node_modules alone
      Flutter app: 2,000 files → 1,500 are generated (.g.dart, Pods/)
    Without filtering, indexing takes forever and search results are garbage.

WHERE IT'S CALLED:
    - scanner.py → scan_repository() filters every discovered file through should_skip()
    - pipeline.py → incremental reindex uses it too

DEPENDENCIES:
    - config.py: for settings.repo_path (to find .codewalkignore)
    - fnmatch: Python stdlib glob matching

=============================================================================
"""

# ─── Imports ─────────────────────────────────────────────────────────

from pathlib import Path
import fnmatch as fnmatch_module  # Glob pattern matching (*.dart, etc.)


# =============================================================================
# KEEP_DOT_DIRS — Dot Folders We Actually Want to Index
# =============================================================================
# Most dot-folders are hidden system/tool folders (.git, .vscode, .idea).
# But .github/ contains CI workflows and action configs — that's useful code.

KEEP_DOT_DIRS = {
    ".github",
}


# =============================================================================
# SKIP_DIRS — Directory Names to Always Skip
# =============================================================================
# If ANY path segment matches one of these, skip the entire file.
# Example: "src/node_modules/lodash/index.js" → skip (has "node_modules")

SKIP_DIRS = {
    # ─── JavaScript / TypeScript ─────────────────────────
    "node_modules",       # npm packages (thousands of files)
    "bower_components",   # Legacy JS package manager

    # ─── Python ──────────────────────────────────────────
    "__pycache__",        # Compiled .pyc files
    "venv", ".venv",     # Virtual environments
    "env", ".env",       # Alt virtual env names
    "egg-info",          # Python package metadata
    ".codewalk-env",     # Our own venv

    # ─── Generic Build Output ────────────────────────────
    "dist",              # Bundled/compiled output
    "build",            # Build artifacts
    "target",           # Java/Rust build output
    "coverage",         # Test coverage reports

    # ─── iOS / macOS ─────────────────────────────────────
    "Pods",             # CocoaPods dependencies
    "DerivedData",      # Xcode build cache
    "Carthage",         # Alt iOS dependency manager

    # ─── Flutter / Dart ──────────────────────────────────
    "ephemeral",        # Flutter web build cache
    ".dart_tool",       # Dart SDK cache

    # ─── Go / Ruby / PHP ────────────────────────────────
    "vendor",           # Vendored dependencies
    "deps",             # Elixir/Mix dependencies

    # ─── Swift ───────────────────────────────────────────
    "Packages",         # Swift Package Manager

    # ─── Elixir ──────────────────────────────────────────
    "_build",           # Mix build output

    # ─── Testing ─────────────────────────────────────────
    "__tests__",        # Jest test folders
    "__snapshots__",    # Jest snapshot files
    "testdata",         # Test fixtures
    "fixtures",         # Test data

    # ─── Localization ────────────────────────────────────
    "l10n", "locales", "i18n",  # Translation files

    # ─── Migrations ──────────────────────────────────────
    "migrations",       # DB migration files (auto-generated SQL)

    # ─── Build Tool Caches ───────────────────────────────
    ".gradle",          # Gradle build cache
    ".next",            # Next.js build output
    ".nuxt",            # Nuxt.js build output
    ".svelte-kit",      # SvelteKit build
    ".turbo",           # Turborepo cache
    ".nx",              # Nx monorepo cache
    ".terraform",       # Terraform provider cache

    # ─── Python Tooling ──────────────────────────────────
    ".mypy_cache",      # MyPy type checker cache
    ".pytest_cache",    # Pytest cache
    ".ruff_cache",      # Ruff linter cache
    ".tox",             # Tox test environments
    "htmlcov",          # Coverage HTML reports
    ".eggs",            # setuptools build eggs
    "__pypackages__",   # PEP 582 packages
    ".hypothesis",      # Property-based testing

    # ─── JS Build & Deploy ───────────────────────────────
    ".cache",           # Generic build cache
    ".parcel-cache",    # Parcel bundler
    "storybook-static", # Storybook build
    ".vercel",          # Vercel deploy
    ".netlify",         # Netlify deploy

    # ─── Output / Generated ──────────────────────────────
    "out", "obj",       # .NET / generic output
    "gen", "generated", # Code generation output
    "__generated__",    # Alt generated folder
    "intermediates",    # Android build intermediates

    # ─── IDE / Platform ──────────────────────────────────
    "xcuserdata",       # Xcode per-user settings
    ".cxx",             # Android NDK build

    # ─── Test / CI Artifacts ─────────────────────────────
    ".nyc_output",      # Istanbul coverage
    "test-results",     # CI test output
    "test-reports",     # CI test reports

    # ─── Docs Build ──────────────────────────────────────
    "site",             # MkDocs / Jekyll output

    # ─── Misc ────────────────────────────────────────────
    "tmp", "temp",      # Temporary files
    "logs",             # Log files
    "reports",          # Generated reports
}


# =============================================================================
# SKIP_EXTENSIONS — File Extensions That Aren't Source Code
# =============================================================================
# If a file ends with one of these, skip it.
# Grouped by category for maintainability.

SKIP_EXTENSIONS = {
    # ─── Compiled / Bytecode ─────────────────────────────
    ".pyc", ".pyo", ".pyd",       # Python compiled
    ".class",                      # Java compiled
    ".o", ".obj", ".a", ".lib",   # C/C++ object files
    ".so", ".dylib", ".dll",      # Shared libraries
    ".exe", ".bin",               # Executables
    ".wasm",                       # WebAssembly
    ".dex",                        # Android Dalvik
    ".beam",                       # Erlang/Elixir
    ".hi",                         # Haskell interface
    ".elc",                        # Emacs Lisp compiled
    ".rbc",                        # Ruby compiled
    ".fasl",                       # Common Lisp compiled

    # ─── App Bundles / Archives ──────────────────────────
    ".apk", ".ipa", ".aab",      # Mobile apps
    ".jar", ".aar",              # Java archives
    ".ear", ".war",              # Java EE archives

    # ─── Images ──────────────────────────────────────────
    ".ico", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".bmp", ".tiff", ".webp", ".heic", ".heif",
    ".psd", ".ai", ".sketch", ".fig",

    # ─── 3D / Game Assets ────────────────────────────────
    ".fbx", ".glb", ".gltf", ".blend",
    ".unity", ".prefab",

    # ─── Fonts ───────────────────────────────────────────
    ".woff", ".woff2", ".ttf", ".eot", ".otf",

    # ─── Audio / Video ───────────────────────────────────
    ".mp3", ".wav", ".ogg", ".flac", ".aac",
    ".mp4", ".avi", ".mov", ".mkv", ".webm",

    # ─── Archives ────────────────────────────────────────
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".rar", ".7z", ".tgz",

    # ─── Lock Files ──────────────────────────────────────
    ".lock",  # Generic lock extension

    # ─── Database / Data ─────────────────────────────────
    ".db", ".sqlite", ".sqlite3",
    ".h5", ".hdf5", ".pkl", ".pickle",

    # ─── ML Model Files ──────────────────────────────────
    ".pt", ".pth", ".onnx", ".safetensors",
    ".bin", ".model", ".weights",

    # ─── Documents (Not Code) ────────────────────────────
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".rst", ".txt", ".adoc",

    # ─── Translation / Localization ──────────────────────
    ".arb", ".xliff", ".xlf", ".po", ".pot", ".mo",
    ".strings", ".stringsdict",

    # ─── Certificates / Secrets ──────────────────────────
    ".pem", ".crt", ".key", ".p12", ".pfx",
    ".secret", ".secrets", ".age",
    ".env.local", ".env.production",
    ".jks", ".keystore",
    ".cer", ".der", ".p8",
    ".mobileprovision",

    # ─── Terraform ───────────────────────────────────────
    ".tfstate", ".tfvars",

    # ─── Source Maps / Notebooks ─────────────────────────
    ".map",                        # JS source maps
    ".ipynb",                      # Jupyter (JSON blobs)

    # ─── Xcode / iOS Generated ───────────────────────────
    ".pbxproj",                    # Xcode project file (huge XML)
    ".xcscheme",
    ".storyboard", ".xib",

    # ─── GPU Shaders ─────────────────────────────────────
    ".glsl", ".hlsl", ".vert", ".frag",
    ".spv",                        # SPIR-V compiled
    ".metal",                      # Apple Metal

    # ─── Debug / Build ───────────────────────────────────
    ".pdb",                        # Windows debug symbols
    ".res",                        # Windows resources

    # ─── Patch / Diff ────────────────────────────────────
    ".patch", ".diff",

    # ─── Temp / Logs ─────────────────────────────────────
    ".tmp", ".temp",
    ".bak", ".orig",
    ".log", ".cache",

    # ─── Editor Files ────────────────────────────────────
    ".swp", ".swo", ".swn",       # Vim swap files
    ".iml",                        # IntelliJ module file

    # ─── Profiling / Coverage ────────────────────────────
    ".prof", ".cpuprofile",
    ".dmp", ".hprof",
    ".profdata", ".profraw",
    ".lcov",
    ".gcda", ".gcno",

    # ─── Compiler Artifacts ──────────────────────────────
    ".d",                          # GCC/Clang dependency files
    ".pch", ".gch",               # Precompiled headers
    ".rlib",                       # Rust compiled library
    ".jmod",                       # Java module

    # ─── Unity Specific ──────────────────────────────────
    ".meta",                       # Auto-generated per asset
    ".anim", ".controller",
    ".lighting", ".shadergraph",

    # ─── Visual Studio ───────────────────────────────────
    ".suo", ".sdf", ".ncb",
    ".user",

    # ─── R / MATLAB ──────────────────────────────────────
    ".rda", ".rds", ".rdata",
    ".mat",

    # ─── Ops / Infra ─────────────────────────────────────
    ".tfplan",                     # Terraform plan
    ".retry",                      # Ansible retry
    ".webmanifest",

    # ─── Binary Data ─────────────────────────────────────
    ".dat", ".data", ".npy", ".npz",
    ".parquet", ".feather", ".arrow",
    ".tfrecord", ".coverage",
}


# =============================================================================
# SKIP_FILES — Specific Filenames to Always Skip
# =============================================================================
# These are generated lock/config files that are never hand-written code.

SKIP_FILES = {
    # ─── Package Lock Files ──────────────────────────────
    "package-lock.json",   # npm
    "yarn.lock",           # Yarn
    "pnpm-lock.yaml",     # pnpm
    "pubspec.lock",        # Dart/Flutter
    "Podfile.lock",        # CocoaPods
    "Gemfile.lock",        # Ruby
    "composer.lock",       # PHP
    "Cargo.lock",          # Rust
    "go.sum",              # Go (dependency checksums)
    "flake.lock",          # Nix
    "bun.lockb",           # Bun
    "poetry.lock",         # Python Poetry
    "uv.lock",             # Python uv
    "mix.lock",            # Elixir
    "stack.yaml.lock",     # Haskell
    "deno.lock",           # Deno
    "npm-shrinkwrap.json", # npm (alt lock format)
    ".terraform.lock.hcl", # Terraform

    # ─── OS Junk ─────────────────────────────────────────
    "Thumbs.db",           # Windows thumbnail cache
    "desktop.ini",         # Windows folder settings

    # ─── Python Distribution ─────────────────────────────
    "MANIFEST",            # setuptools manifest
}


# =============================================================================
# SKIP_SUFFIXES — Generated Code File Patterns
# =============================================================================
# If a filename ENDS with one of these, it's auto-generated code.
# Example: "user_model.g.dart" → generated by build_runner → skip

SKIP_SUFFIXES = (
    # ─── Dart Code Generation ────────────────────────────
    ".g.dart",             # json_serializable, built_value
    ".freezed.dart",       # Freezed immutable classes
    ".gen.dart",           # Custom generators
    ".generated.dart",     # Generic generated

    # ─── C# Generated ───────────────────────────────────
    ".g.cs",               # Source generators
    ".designer.cs",        # WinForms designer

    # ─── Protobuf / gRPC ────────────────────────────────
    ".pb.go",              # Go protobuf
    "_pb2.py",             # Python protobuf
    ".pb.swift",           # Swift protobuf
    ".pb.dart",            # Dart protobuf
    "_pb2_grpc.py",        # Python gRPC
    ".grpc.swift",         # Swift gRPC

    # ─── Minified / Bundled JS/CSS ───────────────────────
    ".min.js",             # Minified JavaScript
    ".min.css",            # Minified CSS
    ".bundle.js",          # Webpack bundle
    ".chunk.js",           # Code-split chunks
    ".chunk.css",          # CSS chunks

    # ─── GraphQL Codegen ─────────────────────────────────
    ".graphql.ts",         # Generated TypeScript types
    ".gql.ts",             # Alt GraphQL codegen

    # ─── Go Generated ───────────────────────────────────
    "_generated.go",
    "_mock.go", ".mock.go",

    # ─── JS/TS Generated ────────────────────────────────
    ".generated.ts",
    ".generated.js",
)


# =============================================================================
# should_skip() — The Main Filter Function
# =============================================================================

def should_skip(file_path: str) -> bool:
    """Return True if this file should NOT be indexed.

    EXECUTION FLOW (checks in order, first match = skip):
        1. Hidden dot-folders → skip (except .github)
        2. Hidden dot-files → skip
        3. Junk directories → skip
        4. Binary extensions → skip
        5. Specific filenames → skip
        6. Generated suffixes → skip
        7. .codewalkignore patterns → skip
        8. None matched → DON'T skip (index this file!)

    WHY THIS ORDER?
        Cheapest checks first. Checking path.parts is faster than
        loading .codewalkignore from disk and running glob patterns.
    """
    path = Path(file_path)

    # Rule 1: Skip hidden directories (starting with .) EXCEPT whitelisted
    for part in path.parts[:-1]:  # [:-1] = all dirs, not the filename
        if part.startswith(".") and part not in KEEP_DOT_DIRS:
            return True

    # Rule 2: Skip hidden files (starting with .)
    if path.name.startswith("."):
        return True

    # Rule 3: Skip junk directories
    for part in path.parts:
        if part in SKIP_DIRS:
            return True

    # Rule 4: Skip binary/media extensions
    if path.suffix in SKIP_EXTENSIONS:
        return True

    # Rule 5: Skip specific filenames
    if path.name in SKIP_FILES:
        return True

    # Rule 6: Skip generated file patterns
    if any(path.name.endswith(suffix) for suffix in SKIP_SUFFIXES):
        return True

    # Rule 7: Check .codewalkignore patterns (user-defined)
    if _codewalkignore_matches(file_path):
        return True

    return False  # All checks passed → this file should be indexed


# =============================================================================
# .codewalkignore Support — User-Defined Skip Rules
# =============================================================================
# Works like .gitignore but for codewalk indexing.
# Users put a .codewalkignore file in their repo root to skip custom paths.
#
# Example .codewalkignore:
#   scripts/         ← skip entire scripts/ directory
#   *.config.js      ← skip all config JS files
#   legacy_code/     ← skip old code folder

_codewalkignore_patterns: list[str] | None = None  # Cache (loaded once)


def _load_codewalkignore() -> list[str]:
    """Load patterns from .codewalkignore (gitignore-like syntax).

    CACHING: Loaded once, then cached in _codewalkignore_patterns.
    Subsequent calls return the cached list instantly.
    """
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
        if not line or line.startswith("#"):  # Skip comments and blank lines
            continue
        patterns.append(line)

    _codewalkignore_patterns = patterns
    return _codewalkignore_patterns


def _codewalkignore_matches(file_path: str) -> bool:
    """Check if a file matches any .codewalkignore pattern.

    PATTERN TYPES SUPPORTED:
        "scripts/"     → matches if "scripts" is any directory in the path
        "*.config.js"  → glob against filename
        "legacy"       → matches if "legacy" is any path segment
    """
    patterns = _load_codewalkignore()
    if not patterns:
        return False

    for pattern in patterns:
        # Directory pattern (ends with /)
        if pattern.endswith("/"):
            dir_name = pattern.rstrip("/")
            if dir_name in Path(file_path).parts:
                return True
        # Glob pattern (contains * or ?)
        elif fnmatch_module.fnmatch(file_path, pattern):
            return True
        # Also check just the filename
        elif fnmatch_module.fnmatch(Path(file_path).name, pattern):
            return True
        # Plain name matches any path segment
        elif "/" not in pattern and pattern in Path(file_path).parts:
            return True

    return False


def reset_codewalkignore():
    """Reset cached patterns. Call when repo_path changes between analyses."""
    global _codewalkignore_patterns
    _codewalkignore_patterns = None
