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


class ReviewIssueSchema(BaseModel):
    """JSON schema for one issue returned by a reviewer."""
    severity: str = Field(..., pattern=r"^(blocker|error|suggestion)$")
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
    status: str = Field(default="new", pattern=r"^(new|still_present)$")


class ReviewOutputSchema(BaseModel):
    """JSON schema for a complete reviewer output."""
    executive_summary: str = ""
    issues: list[ReviewIssueSchema] = Field(default_factory=list)


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


def _issue_to_finding(issue: ReviewIssueSchema | dict[str, Any]) -> Finding | None:
    """Convert a parsed issue (schema object or raw dict) into a Finding."""
    def _get(key: str, default: Any = None) -> Any:
        if isinstance(issue, dict):
            return issue.get(key, default)
        return getattr(issue, key, default)

    try:
        return Finding(
            severity=Severity(_get("severity", "error")),
            category=Category(_get("category", "bug")),
            file_path=_get("file_path", "unknown"),
            line_number=_get("line_number"),
            title=_get("title", "Untitled"),
            explanation=_get("explanation", ""),
            current_code=_get("current_code"),
            recommended_code=_get("recommended_code"),
            blocking=_get("blocking", False),
            confidence=Confidence(_get("confidence", "medium")),
            source=Source.LLM,
            status=_get("status") or "new",
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


