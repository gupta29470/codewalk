"""Generate a starter codewalk.yaml for a repo based on detected tech stack."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from src.codewalk.ingestion.tech_detect import detect_tech_stack
from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")

# Stack-specific exclusions that are NOT covered by the core safety net in
# file_filter.py (which already skips dependency folders, build artifacts,
# binaries, secrets, lock files, and generated artifacts).
#
# Patterns can be:
#   - plain directory names   (e.g. "tools")
#   - plain filenames         (e.g. "project.json")
#   - glob patterns           (e.g. "*.stories.tsx")
#   - relative paths          (e.g. "apps/*/force_updates")
TECH_STACK_EXCLUDES: dict[str, dict[str, list[str]]] = {
    "nx": {
        "comment": "Nx / TypeScript monorepo",
        "patterns": [
            "tools",
            "scripts",
            "cdk",
            ".storybook",
            "apps/*/force_updates",
            "*.stories.ts",
            "*.stories.tsx",
            "*.stories.js",
            "*.stories.jsx",
            "*.cy.ts",
            "*.cy.tsx",
            "*.cy.js",
            "*.cy.jsx",
            "project.json",
            "nx.json",
        ],
    },
    "typescript": {
        "comment": "TypeScript",
        "patterns": [
            "tools",
            "scripts",
            "cdk",
            ".storybook",
            "coverage",
            "*.stories.ts",
            "*.stories.tsx",
            "*.cy.ts",
            "*.cy.tsx",
        ],
    },
    "javascript/node": {
        "comment": "JavaScript / Node",
        "patterns": [
            "tools",
            "scripts",
            "cdk",
            ".storybook",
            "coverage",
            "*.stories.js",
            "*.stories.jsx",
            "*.cy.js",
            "*.cy.jsx",
        ],
    },
    "python": {
        "comment": "Python",
        "patterns": [
            "tools",
            "scripts",
            "cdk",
            "migrations",
            "notebooks",
            "l10n",
            "locales",
            "i18n",
            "*.ipynb",
        ],
    },
    "dart/flutter": {
        "comment": "Flutter / Dart",
        "patterns": [
            "tools",
            "scripts",
            "cdk",
            "migrations",
            "l10n",
            "locales",
            "i18n",
            "*.mocks.dart",
        ],
    },
    "go": {
        "comment": "Go",
        "patterns": [
            "tools",
            "scripts",
            "cdk",
        ],
    },
    "rust": {
        "comment": "Rust",
        "patterns": [
            "tools",
            "scripts",
            "cdk",
            "examples",
        ],
    },
    "java": {
        "comment": "Java",
        "patterns": [
            "tools",
            "scripts",
            "cdk",
            "migrations",
            "l10n",
            "locales",
            "i18n",
        ],
    },
    "kotlin": {
        "comment": "Kotlin",
        "patterns": [
            "tools",
            "scripts",
            "cdk",
            "migrations",
        ],
    },
    "java/kotlin": {
        "comment": "Java / Kotlin",
        "patterns": [
            "tools",
            "scripts",
            "cdk",
            "migrations",
        ],
    },
    "ruby": {
        "comment": "Ruby",
        "patterns": [
            "tools",
            "scripts",
            "cdk",
        ],
    },
    "php": {
        "comment": "PHP",
        "patterns": [
            "tools",
            "scripts",
            "cdk",
        ],
    },
    "c/cpp": {
        "comment": "C / C++",
        "patterns": [
            "tools",
            "scripts",
            "cdk",
        ],
    },
}


# Minimal default when no stack is detected.
DEFAULT_EXCLUDE_PATTERNS = [
    "tools",
    "scripts",
    "cdk",
]


def _pattern_exists_in_repo(root: Path, pattern: str) -> bool:
    """Return True if pattern's first concrete path segment exists in the repo.

    Glob patterns always return True (they are cheap defaults). Plain filenames
    (no slash, contains a dot) are also included by default because they often
    appear at arbitrary depths (e.g. project.json inside Nx workspaces).
    """
    if any(c in pattern for c in "*?[]"):
        return True

    parts = pattern.split("/")
    first = parts[0]
    if not first:
        return True

    # Plain filenames like project.json / nx.json — include by default.
    # Dot-directories (.storybook, .next, etc.) are still treated as dirs.
    if len(parts) == 1 and first[0] != "." and "." in first:
        return True

    candidate = root / first
    if not candidate.exists():
        return False
    if len(parts) == 1:
        return True
    return candidate.is_dir()


def _collect_excludes(stack: Iterable[str], root: Path) -> list[str]:
    """Merge stack-specific excludes and filter out non-existent concrete paths."""
    seen: set[str] = set()
    ordered: list[str] = []

    for tech in stack:
        entry = TECH_STACK_EXCLUDES.get(tech)
        if not entry:
            continue
        for pattern in entry["patterns"]:
            normalized = pattern.strip()
            if normalized in seen:
                continue
            if not _pattern_exists_in_repo(root, normalized):
                continue
            seen.add(normalized)
            ordered.append(normalized)

    # Fallback defaults for unknown stacks.
    if not ordered:
        for pattern in DEFAULT_EXCLUDE_PATTERNS:
            if _pattern_exists_in_repo(root, pattern):
                ordered.append(pattern)

    return ordered


def _yaml_safe_scalar(value: str) -> str:
    """Quote a YAML scalar if it would otherwise be parsed as a special token."""
    if not value:
        return '""'
    first = value[0]
    # Leading * is an alias, ?/&/!/%/@/` are other YAML special markers.
    needs_quotes = first in "*?&!%@`"
    if needs_quotes:
        return f'"{value}"'
    return value


def _render_yaml(excludes: list[str]) -> str:
    """Render a starter codewalk.yaml string."""
    lines = [
        "# Auto-generated by codewalk_generate_config.",
        "#",
        "# This file lists repo-specific indexing exclusions. A core safety net inside",
        "# Codewalk already skips dependency folders, build artifacts, binaries, secrets,",
        "# lock files, and generated artifacts — so those do not need to be repeated here.",
        "#",
        "# Edit or remove any exclude line to include that content in the index. Use",
        "# indexing.include to override an exclusion for a specific path.",
        "",
        "indexing:",
        "  exclude:",
    ]

    if excludes:
        for pattern in excludes:
            lines.append(f"    - {_yaml_safe_scalar(pattern)}")
    else:
        lines.append("    # No stack-specific exclusions detected. Add patterns here if needed.")

    lines.extend([
        "",
        "  branches:",
        "    - main",
        "    - master",
        "",
        "# Optional: paths to team guidelines and docs for review context.",
        "# Relative to this file.",
        "# guidelines_path: docs/guidelines",
        "# docs_path: docs",
    ])

    return "\n".join(lines) + "\n"


def generate_codewalk_yaml(
    repo_path: str | Path,
    *,
    force: bool = False,
    stack: list[str] | None = None,
) -> Path | None:
    """Create a starter codewalk.yaml for the repo.

    Args:
        repo_path: Path to the repository root.
        force: If True, overwrite an existing codewalk.yaml.
        stack: Optional pre-computed tech stack. If omitted, auto-detected.

    Returns:
        Path to the written file, or None if it already exists and force=False.
    """
    root = Path(repo_path).resolve()
    yaml_path = root / "codewalk.yaml"

    if yaml_path.exists() and not force:
        return None

    detected = stack if stack is not None else detect_tech_stack(str(root))
    excludes = _collect_excludes(detected, root)
    yaml_path.write_text(_render_yaml(excludes), encoding="utf-8")
    _log(f"[config_generator] Wrote codewalk.yaml to {yaml_path} (stack: {detected or 'unknown'})")
    return yaml_path


def build_excludes_for_repo(repo_path: str | Path) -> list[str]:
    """Return the list of stack-specific excludes that would be written.

    Useful for CLI previews without creating a file.
    """
    root = Path(repo_path).resolve()
    stack = detect_tech_stack(str(root))
    return _collect_excludes(stack, root)
