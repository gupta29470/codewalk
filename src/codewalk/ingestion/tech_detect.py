"""
=============================================================================
 tech_detect.py — Technology Stack Detection
=============================================================================

WHAT THIS FILE DOES:
    Looks at what CONFIG FILES exist in a repo's root directory to determine
    what programming languages/frameworks the project uses.
    
    Example: If the repo has pubspec.yaml → it's a Dart/Flutter project.
             If it has package.json → it's a JavaScript/Node project.
             If both exist → it's a multi-language project.

HOW IT WORKS (dead simple):
    1. Receives a repo path (e.g. "/Users/dev/Konnect")
    2. Checks: does requirements.txt exist? → "python"
    3. Checks: does pubspec.yaml exist? → "dart/flutter"
    4. Repeats for all known config files
    5. Returns deduplicated sorted list: ["dart/flutter", "python"]

WHY THIS APPROACH?
    - Zero parsing needed — just file existence checks (fast)
    - Config files are the most reliable signal of a project's stack
    - A repo with pubspec.yaml is ALWAYS a Dart project
    - Deduplication via set() prevents "python" appearing 3x when a repo
      has requirements.txt AND pyproject.toml AND setup.py

WHERE IT'S CALLED:
    - codewalk_get_overview() in server.py → shows tech stack in overview
    - codewalk_analyze_codebase() → detects what kind of project this is

DEPENDENCIES:
    - pathlib.Path: for checking if files exist
    - log.py: for logging what was detected

=============================================================================
"""

# ─── Imports ─────────────────────────────────────────────────────────

import logging
from pathlib import Path

from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")


# =============================================================================
# CONFIG_FILE_MAP — The Detection Rules
# =============================================================================
# 
# KEY = filename to look for in repo root
# VALUE = what technology it indicates
#
# HOW TO READ THIS:
#   "pubspec.yaml": "dart/flutter"
#   means: "If I see pubspec.yaml in the root → this is a Dart/Flutter project"
#
# WHY A DICT?
#   Simple lookup. For each filename in the map, check if it exists → add tech.
#   Adding support for a new language = add ONE line to this dict.

CONFIG_FILE_MAP = {
    # JavaScript / Node.js ecosystem
    "package.json": "javascript/node",    # npm/yarn/pnpm project
    "tsconfig.json": "typescript",         # TypeScript config → definitely TS

    # Dart / Flutter
    "pubspec.yaml": "dart/flutter",        # Flutter/Dart package manifest

    # Python ecosystem (multiple possible config files)
    "requirements.txt": "python",          # pip dependencies
    "pyproject.toml": "python",            # modern Python project config
    "setup.py": "python",                  # legacy Python packaging
    "Pipfile": "python",                   # pipenv dependencies

    # Systems languages
    "Cargo.toml": "rust",                  # Rust package manifest
    "go.mod": "go",                        # Go module definition

    # JVM languages
    "pom.xml": "java",                     # Maven build (Java)
    "build.gradle": "java/kotlin",         # Gradle (Java or Kotlin)
    "build.gradle.kts": "kotlin",          # Kotlin DSL Gradle → definitely Kotlin

    # Other
    "Gemfile": "ruby",                     # Ruby bundler
    "composer.json": "php",                # PHP Composer
    "CMakeLists.txt": "c/cpp",             # CMake → C or C++ project
    "Makefile": "c/cpp",                   # Make → usually C/C++
}


# =============================================================================
# detect_tech_stack() — The Only Function
# =============================================================================

def detect_tech_stack(repo_path: str) -> list[str]:
    """Detect the tech stack by checking which config files exist in the repo root.

    EXECUTION FLOW:
        1. repo_path = "/Users/dev/Konnect"
        2. Loop through CONFIG_FILE_MAP:
             Does /Users/dev/Konnect/pubspec.yaml exist? → YES → add "dart/flutter"
             Does /Users/dev/Konnect/package.json exist? → NO → skip
             Does /Users/dev/Konnect/requirements.txt exist? → NO → skip
        3. detected = {"dart/flutter"} (set = no duplicates)
        4. Return sorted list: ["dart/flutter"]

    WHY A SET?
        A Python project might have BOTH requirements.txt AND pyproject.toml.
        Without a set, we'd return ["python", "python"].
        set() automatically deduplicates → {"python"} → just once.

    Args:
        repo_path: Absolute path to the repo root.

    Returns:
        Sorted list of detected technologies.
        Example: ["dart/flutter", "python"]
    """
    _log(f"[tech_detect] Detecting tech stack: {repo_path}")
    root = Path(repo_path)
    detected = set()  # set = no duplicates allowed
    
    # Check each known config file
    for filename, tech in CONFIG_FILE_MAP.items():
        if (root / filename).exists():  # Path / "filename" = join paths
            detected.add(tech)           # .add() on a set = insert if not present

    return sorted(detected)  # sorted() makes output deterministic
