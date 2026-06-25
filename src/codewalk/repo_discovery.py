"""Repo root discovery and codewalk.yaml initialization.

Codewalk uses `codewalk.yaml` as the repo identity marker. The directory
containing that file is the repo root, and `.codewalk/` is written there.
"""
from __future__ import annotations

import os
from pathlib import Path

from src.codewalk.ingestion.config_generator import generate_codewalk_yaml
from src.codewalk.log import log as _log


DEFAULT_CODEWALK_YAML = """# Codewalk repo configuration
# https://github.com/codewalk-ai/codewalk

indexing:
  exclude:
    - .codewalk/**
    - .git/**
    - node_modules/**
    - __pycache__/**
    - .venv/**
    - venv/**
  branches:
    - main
    - master

# Optional: paths to team guidelines and docs for review context.
# Relative to this file.
# guidelines_path: docs/guidelines
# docs_path: docs

# Optional: tool command overrides.
# tools:
#   static_analysis:
#     python:
#       - ruff check --output-format=json {files}
#   test_command:
#     python:
#       - pytest
"""


class RepoNotFoundError(Exception):
    """Raised when a codewalk.yaml repo marker cannot be found or created."""


def _find_git_root(start_dir: Path) -> Path | None:
    """Return the nearest parent containing a .git folder, or None."""
    for parent in [start_dir, *start_dir.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def find_repo_root(start_dir: str | None = None) -> Path:
    """Walk up from start_dir looking for codewalk.yaml.

    Args:
        start_dir: Directory to start from. Defaults to os.getcwd().

    Returns:
        Path to the directory containing codewalk.yaml.

    Raises:
        RepoNotFoundError: if no codewalk.yaml is found.
    """
    path = Path(start_dir or os.getcwd()).resolve()
    for parent in [path, *path.parents]:
        if (parent / "codewalk.yaml").exists():
            return parent
    raise RepoNotFoundError(
        f"No codewalk.yaml found at or above {path}. "
        "Run Codewalk from inside a repo or create a codewalk.yaml file."
    )


def ensure_codewalk_yaml(
    start_dir: str | None = None,
    *,
    create: bool = True,
    prefer_git_root: bool = True,
) -> Path:
    """Return the repo root and ensure a codewalk.yaml exists.

    Discovery order:
      1. Walk up from start_dir looking for codewalk.yaml.
      2. If none found and prefer_git_root=True, use the nearest .git root.
      3. Otherwise use start_dir (or cwd).
      4. If create=True and no codewalk.yaml exists at the chosen root, create
         a default one.

    Args:
        start_dir: Directory to start from. Defaults to os.getcwd().
        create: Whether to create a default codewalk.yaml if missing.
        prefer_git_root: Prefer the git root as the creation location.

    Returns:
        Path to the directory containing codewalk.yaml.

    Raises:
        RepoNotFoundError: if no codewalk.yaml exists and create=False.
    """
    start = Path(start_dir or os.getcwd()).resolve()

    # 1. Existing codewalk.yaml
    try:
        return find_repo_root(str(start))
    except RepoNotFoundError:
        pass

    # 2. Choose a root for creation
    root = _find_git_root(start) if prefer_git_root else None
    root = root or start

    yaml_path = root / "codewalk.yaml"

    if yaml_path.exists():
        return root

    if not create:
        raise RepoNotFoundError(
            f"No codewalk.yaml found at or above {start}. "
            "Pass create=True to initialize one automatically."
        )

    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    generated = generate_codewalk_yaml(root, force=False)
    if generated is None:
        # Fallback to the minimal default if generation unexpectedly fails.
        _log(f"[repo_discovery] Creating default codewalk.yaml at {yaml_path}")
        yaml_path.write_text(DEFAULT_CODEWALK_YAML, encoding="utf-8")

    return root


def resolve_mcp_workspace_root(
    start_dir: str | None = None,
    *,
    create: bool = True,
) -> Path:
    """Resolve the MCP workspace root, creating codewalk.yaml if needed.

    This is the MCP-facing entry point that prefers the git root and guarantees
    a codewalk.yaml marker exists when ``create=True``.
    """
    return ensure_codewalk_yaml(start_dir, create=create, prefer_git_root=True)
    return root
