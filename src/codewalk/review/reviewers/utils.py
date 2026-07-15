"""Shared helpers for specialized reviewers."""
from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel

if TYPE_CHECKING:
    import threading

T = TypeVar("T")
from pydantic import BaseModel, Field

from src.codewalk.review.diff_parser import DiffFile
from src.codewalk.review.report import Category, Confidence, Finding, Severity, Source

logger = logging.getLogger("codewalk")


class ReviewIssueSchema(BaseModel):
    """JSON schema for one issue returned by a reviewer."""
    severity: str = Field(..., pattern=r"^(critical|warning|suggestion)$")
    category: str = Field(
        ...,
        pattern=r"^(bug|security|type_safety|architecture|error_handling|test|blast_radius|style|design|naming|complexity|logging|privacy|hygiene)$",
    )
    file_path: str
    line_number: int | None = None
    title: str
    explanation: str
    current_code: str | None = None
    recommended_code: str | None = None
    blocking: bool = False
    confidence: str = Field(..., pattern=r"^(high|medium|low)$")


class ReviewOutputSchema(BaseModel):
    """JSON schema for a complete reviewer output."""
    executive_summary: str = ""
    issues: list[ReviewIssueSchema] = Field(default_factory=list)


def _read_file_content(repo_path: Path, file_path: str) -> str:
    full = repo_path / file_path
    if not full.exists():
        return ""
    try:
        return full.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _format_hunks(diff_file: DiffFile) -> str:
    lines: list[str] = []
    for hunk in diff_file.hunks:
        lines.append(
            f"@@ -{hunk.source_start},{hunk.source_length} +{hunk.start_line},{len(hunk.lines)} @@"
        )
        for line in hunk.lines:
            prefix = {"added": "+", "removed": "-", "context": " "}.get(line.change_type, " ")
            lines.append(f"{prefix}{line.content}")
    return "\n".join(lines)


def _build_file_prompt(
    diff_file: DiffFile,
    repo_path: Path,
    reviewer_prompt: str,
    context: "ReviewContext",  # type: ignore[name-defined]
) -> str:
    """Build a per-file prompt for a specialized reviewer."""
    from src.codewalk.review.utils import smart_truncate_file_content

    parts: list[str] = []

    if context.guidelines:
        parts.append(
            "These code guidelines define this repository's standards. "
            "Enforce them fully, but do not limit the review to only these rules (underfitting) "
            "and do not mechanically pattern-match them (overfitting). "
            "Use your broader engineering judgment to flag any issue introduced or worsened by the diff."
        )
        parts.append(f"## Code guidelines\n\n{context.guidelines}")

    parts.append(reviewer_prompt)

    if context.user_prompt:
        parts.append(f"## Team-specific instructions\n\n{context.user_prompt}")

    if context.file_tree:
        # Send relevant subtree near the changed file, capped at 100 paths
        changed_dir = str(Path(diff_file.file_path).parent)
        relevant_tree = [
            p for p in context.file_tree
            if p.startswith(changed_dir) or changed_dir.startswith(str(Path(p).parent))
        ]
        tree_to_send = relevant_tree[:100] if relevant_tree else context.file_tree[:100]
        parts.append("## Repository file tree (relevant subset)\n")
        parts.extend(tree_to_send)
        if len(context.file_tree) > len(tree_to_send):
            parts.append(f"... and {len(context.file_tree) - len(tree_to_send)} more files")
        parts.append("")

    parts.append(f"## File under review: {diff_file.file_path}")
    content = _read_file_content(repo_path, diff_file.file_path)
    if content:
        truncated = smart_truncate_file_content(content, diff_file.hunks, max_tokens=10000)
        parts.append("```")
        parts.append(truncated)
        parts.append("```")
    else:
        parts.append("(file not found or deleted)")

    parts.append("## Diff hunks\n")
    parts.append(_format_hunks(diff_file))

    if context.neighborhood and context.neighborhood.snippets:
        parts.append("\n## Neighborhood context\n")
        for snippet in context.neighborhood.snippets:
            if snippet.file_path == diff_file.file_path:
                continue
            parts.append(f"### {snippet.source}: {snippet.file_path}")
            parts.append("```")
            parts.append(snippet.content)
            parts.append("```")

    if context.risk_annotation and context.risk_annotation.to_prompt_text():
        parts.append("\n## Risk annotation\n")
        parts.append(context.risk_annotation.to_prompt_text())

    return "\n\n".join(parts)


def _build_batch_prompt(
    diff_files: list[DiffFile],
    repo_path: Path,
    reviewer_prompt: str,
    context: "ReviewContext",  # type: ignore[name-defined]
) -> str:
    """Build a prompt for reviewing multiple files in one LLM call.

    Shared context (guidelines, rubrics, file tree, neighborhood) appears once.
    Each file gets its own section with truncated content and diff hunks.
    """
    from src.codewalk.review.static_analysis import RiskAnnotation
    from src.codewalk.review.utils import smart_truncate_file_content

    parts: list[str] = []

    # Stack context header (architecture, state management, data layer, etc.)
    stack_header = context.extra.get("stack_header", "")
    if stack_header:
        parts.append(stack_header)

    if context.guidelines:
        parts.append(
            "These code guidelines define this repository's standards. "
            "Enforce them fully, but do not limit the review to only these rules (underfitting) "
            "and do not mechanically pattern-match them (overfitting). "
            "Use your broader engineering judgment to flag any issue introduced or worsened by the diff."
        )
        parts.append(f"## Code guidelines\n\n{context.guidelines}")

    parts.append(reviewer_prompt)

    if context.user_prompt:
        parts.append(f"## Team-specific instructions\n\n{context.user_prompt}")

    if context.file_tree:
        # Send relevant subtree near changed files, capped at 100 paths
        changed_dirs = {str(Path(df.file_path).parent) for df in diff_files}
        relevant_tree = [
            p for p in context.file_tree
            if any(p.startswith(d) or d.startswith(str(Path(p).parent)) for d in changed_dirs)
        ]
        tree_to_send = relevant_tree[:100] if relevant_tree else context.file_tree[:100]
        parts.append("## Repository file tree (relevant subset)\n")
        parts.extend(tree_to_send)
        if len(context.file_tree) > len(tree_to_send):
            parts.append(f"... and {len(context.file_tree) - len(tree_to_send)} more files")
        parts.append("")

    for diff_file in diff_files:
        parts.append(f"## File under review: {diff_file.file_path}")
        content = _read_file_content(repo_path, diff_file.file_path)
        if content:
            truncated = smart_truncate_file_content(content, diff_file.hunks, max_tokens=10000)
            parts.append("```")
            parts.append(truncated)
            parts.append("```")
        else:
            parts.append("(file not found or deleted)")

        parts.append("## Diff hunks\n")
        parts.append(_format_hunks(diff_file))

        # Per-file risk annotation from static analysis.
        risk_annotations = context.extra.get("risk_annotations", {})
        ra = risk_annotations.get(diff_file.file_path)
        if isinstance(ra, RiskAnnotation) and ra.to_prompt_text():
            parts.append("\n## Risk annotation\n")
            parts.append(ra.to_prompt_text())

    if context.neighborhood and context.neighborhood.snippets:
        parts.append("\n## Neighborhood context\n")
        for snippet in context.neighborhood.snippets:
            parts.append(f"### {snippet.source}: {snippet.file_path}")
            parts.append("```")
            parts.append(snippet.content)
            parts.append("```")

    return "\n\n".join(parts)


def _parse_llm_json(raw_text: str) -> list[dict[str, Any]] | None:
    """Best-effort parse of an LLM JSON response into issue dicts."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("issues", [])
    return None


def _issue_to_finding(issue: ReviewIssueSchema) -> Finding | None:
    try:
        return Finding(
            severity=Severity(issue.severity),
            category=Category(issue.category),
            file_path=issue.file_path,
            line_number=issue.line_number,
            title=issue.title,
            explanation=issue.explanation,
            current_code=issue.current_code,
            recommended_code=issue.recommended_code,
            blocking=issue.blocking,
            confidence=Confidence(issue.confidence),
            source=Source.LLM,
        )
    except (ValueError, TypeError) as e:
        logger.debug(f"[reviewers] dropping malformed issue: {e}")
        return None


def _estimate_prompt_tokens(text: str) -> int:
    """Best-effort token count for prompt/response accounting."""
    from src.codewalk.review.utils import count_tokens

    return count_tokens(text)


def _llm_timeout_seconds() -> float:
    """Return the configured LLM invocation timeout in seconds."""
    return float(os.getenv("CODEWALK_REVIEW_LLM_TIMEOUT_SECONDS", "120.0"))


def _llm_max_retries() -> int:
    """Return the configured number of retries for transient LLM failures."""
    return int(os.getenv("CODEWALK_REVIEW_LLM_MAX_RETRIES", "2"))


def _is_retryable_error(exc: Exception) -> bool:
    """Return True if an exception looks transient and worth retrying."""
    msg = str(exc).lower()
    retryable = (
        "timeout",
        "timed out",
        "rate limit",
        "too many requests",
        "connection",
        "temporarily unavailable",
        "server error",
        "500",
        "502",
        "503",
        "504",
    )
    return any(r in msg for r in retryable)


def _invoke_with_timeout_and_retry(
    fn: Callable[[], T],
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    operation_name: str = "LLM invocation",
    cancel_event: "threading.Event | None" = None,
) -> T:
    """Run a callable in a thread with a hard timeout and retry logic.

    LangChain invocations can hang on network or provider errors. This wrapper
    aborts after the timeout and retries transient failures with exponential
    backoff. If a cancel event is provided and is set, the invocation aborts
    immediately.
    """
    timeout_seconds = timeout_seconds or _llm_timeout_seconds()
    max_retries = max_retries if max_retries is not None else _llm_max_retries()
    last_exception: Exception | None = None

    for attempt in range(max_retries + 1):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError(f"{operation_name} cancelled")

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(fn)
            try:
                return future.result(timeout=timeout_seconds)
            except FutureTimeoutError:
                future.cancel()
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError(f"{operation_name} cancelled") from None
                last_exception = RuntimeError(
                    f"{operation_name} timed out after {timeout_seconds}s"
                )
            except Exception as e:
                last_exception = e

        if attempt < max_retries and last_exception and _is_retryable_error(last_exception):
            backoff = 2 ** attempt
            logger.warning(
                f"{operation_name} failed (attempt {attempt + 1}/{max_retries + 1}), "
                f"retrying in {backoff}s: {last_exception}"
            )
            time.sleep(backoff)
        else:
            break

    raise last_exception or RuntimeError(f"{operation_name} failed")


def run_structured_review(
    llm: BaseChatModel,
    user_content: str,
    cancel_event: "threading.Event | None" = None,
    system_prompt: str | None = None,
) -> tuple[list[Finding], int]:
    """Invoke the LLM and parse the structured review output.

    Falls back to raw JSON parsing if structured output fails.  Raises only if
    both paths fail so the caller can fail the review rather than silently
    approve.

    Args:
        llm: Language model to invoke.
        user_content: The user prompt content (diff, file content, context).
        cancel_event: Optional cancellation event. If set, the invocation aborts.
        system_prompt: Optional system prompt (reviewer rubric + rules). When
            provided, placed in the system message for higher instruction priority.

    Returns:
        (findings, token_usage) where token_usage is an estimate of prompt +
        response tokens consumed.
    """
    system_content = system_prompt or (
        "You are a senior staff engineer performing a pre-merge code review. "
        "Return valid JSON only."
    )
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]
    prompt_text = "\n".join(m["content"] for m in messages)
    prompt_tokens = _estimate_prompt_tokens(prompt_text)

    timeout_seconds = _llm_timeout_seconds()
    max_retries = _llm_max_retries()

    try:
        structured = llm.with_structured_output(ReviewOutputSchema, method="json_mode")
        response = _invoke_with_timeout_and_retry(
            lambda: structured.invoke(messages),
            timeout_seconds,
            max_retries,
            operation_name="structured review",
            cancel_event=cancel_event,
        )
        findings: list[Finding] = []
        for issue in (response.issues or []):
            finding = _issue_to_finding(issue)
            if finding:
                findings.append(finding)
        # Estimate response tokens from the number of findings and a small base.
        response_tokens = 50 + len(findings) * 80
        return findings, prompt_tokens + response_tokens
    except Exception as structured_err:
        try:
            raw = _invoke_with_timeout_and_retry(
                lambda: llm.invoke(messages),
                timeout_seconds,
                max_retries,
                operation_name="raw review fallback",
                cancel_event=cancel_event,
            )
            content = raw.content if hasattr(raw, "content") else str(raw)
            items = _parse_llm_json(content)
            if items is None:
                raise RuntimeError(
                    f"LLM review failed: structured output error: {structured_err}; raw fallback produced unparseable JSON"
                ) from structured_err
            findings = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    findings.append(
                        Finding(
                            severity=Severity(item.get("severity", "error")),
                            category=Category(item.get("category", "bug")),
                            file_path=item.get("file_path", "unknown"),
                            line_number=item.get("line_number"),
                            title=item.get("title", "Untitled"),
                            explanation=item.get("explanation", ""),
                            current_code=item.get("current_code"),
                            recommended_code=item.get("recommended_code"),
                            blocking=item.get("blocking", False),
                            confidence=Confidence(item.get("confidence", "medium")),
                            source=Source.LLM,
                        )
                    )
                except (ValueError, TypeError):
                    continue
            response_tokens = _estimate_prompt_tokens(content)
            return findings, prompt_tokens + response_tokens
        except Exception as raw_err:
            raise RuntimeError(
                f"LLM review failed: structured output error: {structured_err}; raw fallback error: {raw_err}"
            ) from raw_err


def run_batch_review(
    llm: BaseChatModel,
    diff_files: list[DiffFile],
    context: "ReviewContext",  # type: ignore[name-defined]
    reviewer_prompt: str,
    cancel_event: "threading.Event | None" = None,
) -> tuple[list[Finding], int]:
    """Review a batch of changed files in a single LLM call.

    Returns (findings, token_usage) where token_usage is an estimate.
    """
    from src.codewalk.review.report import Category, Confidence, Finding, Severity, Source

    user_content = _build_batch_prompt(diff_files, context.repo_path, reviewer_prompt, context)
    # System message carries the reviewer rubric for higher instruction priority.
    # User message carries the dynamic content (diff, files, context).
    messages = [
        {"role": "system", "content": reviewer_prompt},
        {"role": "user", "content": user_content},
    ]
    prompt_text = "\n".join(m["content"] for m in messages)
    prompt_tokens = _estimate_prompt_tokens(prompt_text)

    timeout_seconds = _llm_timeout_seconds()
    max_retries = _llm_max_retries()

    try:
        structured = llm.with_structured_output(ReviewOutputSchema, method="json_mode")
        response = _invoke_with_timeout_and_retry(
            lambda: structured.invoke(messages),
            timeout_seconds,
            max_retries,
            operation_name="batched structured review",
            cancel_event=cancel_event,
        )
        findings: list[Finding] = []
        for issue in (response.issues or []):
            finding = _issue_to_finding(issue)
            if finding:
                findings.append(finding)
        response_tokens = 50 + len(findings) * 80
        return findings, prompt_tokens + response_tokens
    except Exception as structured_err:
        try:
            raw = _invoke_with_timeout_and_retry(
                lambda: llm.invoke(messages),
                timeout_seconds,
                max_retries,
                operation_name="batched raw review fallback",
                cancel_event=cancel_event,
            )
            content = raw.content if hasattr(raw, "content") else str(raw)
            items = _parse_llm_json(content)
            if items is None:
                raise RuntimeError(
                    f"Batched LLM review failed: structured output error: {structured_err}; "
                    f"raw fallback produced unparseable JSON"
                ) from structured_err
            findings = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    findings.append(
                        Finding(
                            severity=Severity(item.get("severity", "error")),
                            category=Category(item.get("category", "bug")),
                            file_path=item.get("file_path", "unknown"),
                            line_number=item.get("line_number"),
                            title=item.get("title", "Untitled"),
                            explanation=item.get("explanation", ""),
                            current_code=item.get("current_code"),
                            recommended_code=item.get("recommended_code"),
                            blocking=item.get("blocking", False),
                            confidence=Confidence(item.get("confidence", "medium")),
                            source=Source.LLM,
                        )
                    )
                except (ValueError, TypeError):
                    continue
            response_tokens = _estimate_prompt_tokens(content)
            return findings, prompt_tokens + response_tokens
        except Exception as raw_err:
            raise RuntimeError(
                f"Batched LLM review failed: structured output error: {structured_err}; "
                f"raw fallback error: {raw_err}"
            ) from raw_err
