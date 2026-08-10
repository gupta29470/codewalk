"""Unified batch context builder shared by API and MCP review paths.

Both paths now use the same context string. The API path feeds it to its own
LLM and parses structured findings; the MCP path returns it to the host LLM.
"""
from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.codewalk.review.diff_parser import DiffFile
from src.codewalk.review.neighborhood import expand_neighborhood
from src.codewalk.review.report import Finding
from src.codewalk.review.rubric_loader import Rubrics
from src.codewalk.review.reviewers.utils import (
    _read_file_content,
    format_capped_diff,
)
from src.codewalk.review.utils import smart_truncate_file_content

if TYPE_CHECKING:
    from src.codewalk.review.static_analysis import StaticAnalysisResult

logger = logging.getLogger(__name__)


# Host-LLM instructions prepended to every MCP batch. The API path uses
# `_UNIFIED_REVIEW_SYSTEM_PROMPT` instead (structured JSON output contract).
REVIEW_INSTRUCTIONS = """# Code Review

Use the repository context, rubrics, and risk annotations below to find
concrete, actionable issues introduced or worsened by this diff.

Do not praise. Do not flag style nits unless they indicate a real bug. Only
flag issues caused or worsened by the current diff. Provide a concrete fix
for every issue you report.

## Severity
- **blocker**: security vulnerability, crash, data loss, race condition, breaking API
  change, PII exposure
- **error**: logic error, missing edge case, unsafe pattern, type issue, untested
  new business logic
- **suggestion**: readability, naming, minor consistency

## Finding fields (required on submit)
Each finding must include:
- `file_path`, `line_number`, `severity`, `category`, `title`, `explanation`
- `current_code`, `recommended_code`, `blocking`
- `category`: one of bug | security | type_safety | architecture | error_handling |
  test | blast_radius | style | design | naming | complexity | logging | privacy | hygiene

Call `codewalk_submit_batch_findings` with your findings for this batch."""


_CHARS_PER_TOKEN = 3
_DEFAULT_FILE_TOKEN_CAP = 40_000
_DEFAULT_DIFF_TOKEN_CAP = 20_000


def estimate_shared_context_tokens(*text_blocks: str) -> int:
    """Estimate tokens for content repeated in every batch (instructions, stack, rubrics, guidelines)."""
    total_chars = sum(len(block) for block in text_blocks if block)
    return total_chars // _CHARS_PER_TOKEN


_UNIFIED_REVIEW_SYSTEM_PROMPT = """# Principal Software Engineer — Code Review

You are a principal software engineer reviewing a pull request diff. Use the
repository context, rubrics, and risk annotations provided below to find
concrete, actionable issues introduced or worsened by the changes.

Do not praise. Do not flag style nits unless they indicate a real bug.
Only flag issues caused or worsened by the current diff.
Provide a concrete fix for every issue.

## Severity
- **blocker**: security vulnerability, crash, data loss, race condition, breaking API change, PII exposure
- **error**: logic error, missing edge case, unsafe pattern, type issue, untested new business logic
- **suggestion**: readability, naming, minor consistency

## Output
Return a JSON object with an `issues` array. Each issue must include:
- `severity`: "blocker" | "error" | "suggestion"
- `category`: "bug" | "security" | "type_safety" | "architecture" | "error_handling" | "test" | "blast_radius" | "style" | "design" | "naming" | "complexity" | "logging" | "privacy" | "hygiene"
- `file_path`, `line_number`, `title`, `explanation`
- `current_code`: exact snippet from the diff
- `recommended_code`: corrected snippet or null
- `blocking`: true if must fix before merge
- `confidence`: "high" | "medium" | "low"
- `status`: "new" if introduced or worsened by the diff, or "still_present" if it matches a previous finding listed above and remains valid

Return valid JSON only.
"""


def _format_previous_findings(
    diff_files: list[DiffFile],
    previous_findings: list[Finding],
    neighborhood_snippets: list[Any] | None = None,
    max_findings: int = 50,
) -> str:
    """Format previous findings relevant to the current batch."""
    if not previous_findings:
        return ""

    relevant_files = {df.file_path for df in diff_files}
    if neighborhood_snippets:
        relevant_files.update(s.file_path for s in neighborhood_snippets)

    matched = [
        f for f in previous_findings
        if getattr(f, "file_path", None) in relevant_files
    ]
    matched = matched[:max_findings]

    if not matched:
        return ""

    lines = ["## Previous review findings (for context only)", ""]
    for f in matched:
        severity = getattr(f, "severity", "")
        line = f"- [{severity}] {f.file_path}"
        if getattr(f, "line_number", None):
            line += f":{f.line_number}"
        title = getattr(f, "title", "")
        if title:
            line += f" — {title}"
        lines.append(line)

    lines.append("")
    lines.append(
        "These issues were flagged in an earlier review of related files. "
        "Do not blindly repeat them. Only report them again if they are still "
        "valid and caused or worsened by the current diff. "
        "For each new issue you report, set `status` to `new` unless it is the "
        "same issue as one above, in which case set `status` to `still_present`."
    )
    return "\n".join(lines)


def _format_rubrics(rubrics: Rubrics) -> str:
    """Format rubrics for inclusion in the batch context."""
    parts: list[str] = ["## Review Rubric"]
    if rubrics.core:
        parts.append(rubrics.core)
    lang_parts = [r for _, r in sorted(rubrics.language.items())]
    if lang_parts:
        parts.append("\n".join(lang_parts))
    if rubrics.framework:
        parts.append(rubrics.framework)
    if rubrics.fallback:
        parts.append(rubrics.fallback)
    return "\n\n".join(parts)


def _git_recent_commits(repo_path: Path, file_path: str, n: int = 3) -> str:
    """Return last N commit oneline summaries for a file, or empty string on failure."""
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"-{n}", "--", file_path],
            capture_output=True,
            text=True,
            cwd=str(repo_path),
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        logger.debug("git log failed for %s", file_path)
    return ""


def build_unified_batch_context(
    repo_path: Path,
    batch: list[DiffFile],
    static_result: "StaticAnalysisResult",
    stack_header: str,
    rubrics: Rubrics,
    guidelines: str = "",
    user_prompt: str = "",
    previous_findings: list[Finding] | None = None,
    cancel_event: threading.Event | None = None,
    file_token_cap: int = _DEFAULT_FILE_TOKEN_CAP,
    diff_token_cap: int = _DEFAULT_DIFF_TOKEN_CAP,
    include_host_instructions: bool = False,
    deep: bool = False,
) -> str:
    """Build a single review context string shared by API and MCP paths.

    The returned markdown string contains stack context, rubrics, guidelines,
    previous findings, per-file content + diffs + risk annotations, and
    neighborhood context.

    Diff policy (avoids content+diff duplication on large new files):
      - new files: file content only (smart truncated)
      - modified/deleted: truncated content + capped diff (all ``-`` lines kept)

    When ``include_host_instructions`` is True (MCP batched review), prepends
    ``REVIEW_INSTRUCTIONS`` so the host LLM gets severity/category/submit
    guidance inside every batch. The API path leaves this False and uses
    ``_UNIFIED_REVIEW_SYSTEM_PROMPT`` as the LLM system message instead.
    """
    # Lazy import to avoid circular dependency with engine.py.
    from src.codewalk.review.engine import _load_graph_runtime

    parts: list[str] = []

    if include_host_instructions:
        parts.append(REVIEW_INSTRUCTIONS)

    if stack_header:
        parts.append(stack_header)

    if guidelines:
        parts.append(
            "These code guidelines define this repository's standards. "
            "Enforce them fully, but do not limit the review to only these rules (underfitting) "
            "and do not mechanically pattern-match them (overfitting). "
            "Use your broader engineering judgment to flag any issue introduced or worsened by the diff."
        )
        parts.append(f"## Code guidelines\n\n{guidelines}")

    parts.append(_format_rubrics(rubrics))

    if user_prompt:
        parts.append(f"## Team-specific instructions\n\n{user_prompt}")

    # Neighborhood context.
    deep_mode = deep or (len(batch) == 1 and file_token_cap > _DEFAULT_FILE_TOKEN_CAP)
    graph_runtime, owns_runtime = _load_graph_runtime(repo_path)
    graph_store = graph_runtime.store if graph_runtime and hasattr(graph_runtime, "store") else None
    try:
        neighborhood = expand_neighborhood(
            repo_path,
            batch,
            graph_store=graph_store,
            max_tokens=60_000 if deep_mode else 30_000,
            deep=deep_mode,
        )
    finally:
        if owns_runtime and graph_runtime is not None and hasattr(graph_runtime, "store"):
            try:
                graph_runtime.store.close()
            except Exception:
                pass

    previous_findings_text = _format_previous_findings(
        batch,
        previous_findings or [],
        neighborhood.snippets if neighborhood else None,
    )
    if previous_findings_text:
        parts.append(previous_findings_text)

    # Per-file context.
    for df in batch:
        ra = static_result.risk_annotations.get(df.file_path)
        parts.append(f"### {df.file_path} (+{df.added_lines}/-{df.removed_lines})")
        if ra and ra.to_prompt_text():
            parts.append(f"> {ra.to_prompt_text()}")
        parts.append("")

        content = _read_file_content(repo_path, df.file_path)
        if content:
            truncated = smart_truncate_file_content(content, df.hunks, max_tokens=file_token_cap)
            parts.append("```")
            parts.append(truncated)
            parts.append("```")
        elif df.is_deleted:
            parts.append("*(file deleted)*")
        else:
            parts.append("*(file deleted or not found)*")

        # New files: content-only (diff would duplicate the whole file).
        # Modified/deleted: include a capped diff so removals stay visible.
        if not df.is_new_file:
            capped = format_capped_diff(df, max_tokens=diff_token_cap)
            if capped:
                parts.append("\n**Diff:**")
                parts.append("```diff")
                parts.append(capped)
                parts.append("```")
        else:
            parts.append("\n*(new file — content shown above; diff omitted to avoid duplication)*")

        # In single-file mode, add recent commit history for extra context
        if deep_mode:
            git_log = _git_recent_commits(repo_path, df.file_path)
            if git_log:
                parts.append("\n**Recent commits:**")
                parts.append(f"```\n{git_log}\n```")

        parts.append("")

    if neighborhood and neighborhood.snippets:
        parts.append("## Neighborhood Context (callers, tests)\n")
        for snippet in neighborhood.snippets[:10]:
            parts.append(f"**{snippet.source}:** `{snippet.file_path}`")
            parts.append("```")
            parts.append(snippet.content)
            parts.append("```")
            parts.append("")

    return "\n".join(parts)
