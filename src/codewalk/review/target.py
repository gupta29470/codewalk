"""Resolve which git ref a review should compare against.

Review must not silently assume ``main``/``master``. When the caller has not
named a base (and has not asked for staged-only or a specific commit), the
review entry points (MCP tools and API endpoints) ask for clarification
instead of starting a session.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_CURRENT_ALIASES = frozenset({"current", "current-branch", "."})


def is_current_branch_alias(target_branch: str | None) -> bool:
    """True when ``target_branch`` means local work on the current branch."""
    if target_branch is None:
        return False
    return target_branch.strip().lower() in _CURRENT_ALIASES


def needs_review_target(
    target_branch: str | None, *, staged: bool, commit: str | None
) -> bool:
    """True when the caller has not clearly said what to review against."""
    if staged or commit:
        return False
    return target_branch is None or not target_branch.strip()


def resolve_diff_target_branch(target_branch: str | None) -> str | None:
    """Map a user-facing target to the value ``get_diff`` should receive.

    ``current`` (and aliases) become ``None`` so the diff is local changes on
    this branch (staged + unstaged + untracked). Named branches are kept as-is.
    """
    if target_branch is None:
        return None
    stripped = target_branch.strip()
    if not stripped or is_current_branch_alias(stripped):
        return None
    return stripped


def _list_local_branches(repo_root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "branch", "--format=%(refname:short)"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def format_ask_for_review_target(repo_root: Path) -> str:
    """Prompt the host agent to ask the user which branch to review against."""
    branches = _list_local_branches(repo_root)
    branch_hint = ", ".join(f"`{b}`" for b in branches[:12]) if branches else "`main`, `develop`"

    return "\n".join(
        [
            "Review target not specified.",
            "",
            "Ask the user which branch to review against before starting a review.",
            "Do NOT assume `main`, `master`, or any other default branch.",
            "",
            "Once they answer, call again with:",
            "- `target_branch=\"current\"` — review this branch's local work "
            "(staged + unstaged + untracked / committed WIP on the working tree)",
            f"- `target_branch=\"<branch>\"` — review commits and uncommitted work "
            f"on the current branch against that base (e.g. {branch_hint})",
        ]
    )
