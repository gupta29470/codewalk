"""Deterministic review-context gathering service.

No LLM calls. Produces the raw diff, blast radius, caller context,
guidelines/docs context, and architecture context needed for a review.
Used by the API review endpoints and by MCP review-context tools.
"""
from __future__ import annotations

from src.codewalk.review.reviewer import prepare_review_context, ReviewContext


def gather_context(
    staged: bool = False,
    target_branch: str | None = None,
    commit: str | None = None,
    store=None,
    deps: dict | None = None,
    repo_path: str | None = None,
    graph_store=None,
) -> ReviewContext | None:
    """Gather raw review context. Returns None if there is no diff."""
    return prepare_review_context(
        staged=staged,
        target_branch=target_branch,
        commit=commit,
        store=store,
        deps=deps,
        repo_path=repo_path,
        graph_store=graph_store,
    )
