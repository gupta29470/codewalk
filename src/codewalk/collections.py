"""Chroma collection naming — single source of truth for MCP, API, pipeline, cloud."""

from __future__ import annotations

import json
import os


def code_collection_name(repo_path: str) -> str:
    """Chroma collection prefix for a repo path (reads manifest when present)."""
    path = repo_path.rstrip("/")
    folder_name = path.split("/")[-1] or "codebase"
    manifest_path = f"{path}/.codewalk/manifest.json"
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path) as f:
                meta = json.load(f)
            if meta.get("collection_name"):
                return meta["collection_name"]
            if meta.get("commit_sha"):
                return "codebase"
        except (json.JSONDecodeError, OSError):
            pass
    return folder_name


def docs_collection_name(repo_path: str) -> str:
    """Doc collection name paired with code_collection_name for the same repo."""
    return docs_collection_for_code(code_collection_name(repo_path))


def code_collection_for_slug(repo_full_name: str) -> str:
    """Chroma code collection from GitHub slug (owner/repo → repo). Used by cloud indexer."""
    if not repo_full_name:
        return "codebase"
    name = repo_full_name.rsplit("/", 1)[-1].strip()
    return name or "codebase"


def docs_collection_for_code(code_collection: str) -> str:
    """Doc collection paired with an explicit code collection name."""
    return f"{code_collection}_docs"
