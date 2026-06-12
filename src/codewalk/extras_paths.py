"""Resolve guidelines/docs folders for indexing (yaml → env fallback).

Search/query uses Chroma collections under {repo}/.codewalk/chroma — not these paths.
Paths are only needed when embedding or force-reindexing extras.
"""

from __future__ import annotations

import os

from src.codewalk.config import settings
from src.codewalk.team_config import load_codewalk_yaml


def _abs_under_repo(repo_root: str, relative: str) -> str:
    return os.path.join(repo_root.rstrip("/"), relative.strip("/"))


def resolve_guidelines_path(repo_path: str) -> str:
    """Folder to index review guidelines from.

    Priority:
      1. codewalk.yaml ``guidelines_path`` (relative to repo) if directory exists
      2. REVIEW_GUIDELINES_PATH env / settings (mcp.json local path)
    """
    root = repo_path.rstrip("/")
    config = load_codewalk_yaml(root)
    if config.guidelines_path:
        candidate = _abs_under_repo(root, config.guidelines_path)
        if os.path.isdir(candidate):
            return candidate

    env_path = os.getenv("REVIEW_GUIDELINES_PATH", "") or settings.review_guidelines_path
    if env_path and os.path.isdir(env_path):
        return env_path
    return ""


def resolve_docs_path(repo_path: str) -> str:
    """Folder to index team docs from.

    Priority:
      1. codewalk.yaml ``docs_path`` (relative to repo) if directory exists
      2. CODE_DOCS_PATH env / settings (mcp.json local path)
    """
    root = repo_path.rstrip("/")
    config = load_codewalk_yaml(root)
    if config.docs_path:
        candidate = _abs_under_repo(root, config.docs_path)
        if os.path.isdir(candidate):
            return candidate

    env_path = os.getenv("CODE_DOCS_PATH", "") or settings.code_docs_path
    if env_path and os.path.isdir(env_path):
        return env_path
    return ""
