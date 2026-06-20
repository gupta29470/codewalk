import logging
from pathlib import Path

from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")

CONFIG_FILE_MAP = {
    "package.json": "javascript/node",
    "tsconfig.json": "typescript",
    "nx.json": "nx",
    "pubspec.yaml": "dart/flutter",
    "requirements.txt": "python",
    "pyproject.toml": "python",
    "setup.py": "python",
    "Pipfile": "python",
    "Cargo.toml": "rust",
    "go.mod": "go",
    "pom.xml": "java",
    "build.gradle": "java/kotlin",
    "build.gradle.kts": "kotlin",
    "Gemfile": "ruby",
    "composer.json": "php",
    "CMakeLists.txt": "c/cpp",
    "Makefile": "c/cpp",
}

def detect_tech_stack(repo_path: str) -> list[str]:
    _log(f"[tech_detect] Detecting tech stack: {repo_path}")
    """Detect the tech stack of a repo by checking for config files.

    Returns a list of detected technologies, e.g. ["python", "typescript"].
    Deduplicates — if both requirements.txt AND pyproject.toml exist,
    "python" only appears once.
    """
    root = Path(repo_path)
    detected = set()
    
    for filename, tech in CONFIG_FILE_MAP.items():
        if (root / filename).exists():
            detected.add(tech)

    
    return sorted(detected)