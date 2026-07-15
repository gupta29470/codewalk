"""Verification stage of the review pipeline."""
from __future__ import annotations
import threading

from langchain_core.language_models.chat_models import BaseChatModel

from src.codewalk.review.adversarial import (
    VerificationBatch,
    apply_verification_results,
    build_verification_prompt,
    parse_verification_results,
    prepare_verification_batch,
)
from src.codewalk.review.report import Finding
from src.codewalk.review.reviewers.utils import (
    _invoke_with_timeout_and_retry,
    _llm_max_retries,
    _llm_timeout_seconds,
)


def verify(
    findings: list[Finding],
    llm: BaseChatModel,
    batch: VerificationBatch | None = None,
    cancel_event: "threading.Event | None" = None,
) -> list[Finding]:
    """Run adversarial verification on findings.

    Drops false positives and weakens confidence where appropriate.

    Args:
        findings: Findings to verify.
        llm: Language model used for verification.
        batch: Optional pre-built verification batch. If None, one is built from
            the findings list using priority buckets.
        cancel_event: Optional cancellation event. If set, verification aborts.

    Returns:
        Verified findings.
    """
    if batch is None:
        batch = prepare_verification_batch(findings)
    if not batch:
        return findings

    prompt = build_verification_prompt(batch)
    messages = [
        {"role": "system", "content": "You are a skeptical senior engineer. Return valid JSON only."},
        {"role": "user", "content": prompt},
    ]

    try:
        raw = _invoke_with_timeout_and_retry(
            lambda: llm.invoke(messages),
            timeout_seconds=_llm_timeout_seconds(),
            max_retries=_llm_max_retries(),
            operation_name="adversarial verification",
            cancel_event=cancel_event,
        )
        content = raw.content if hasattr(raw, "content") else str(raw)
        results = parse_verification_results(content)
        return apply_verification_results(findings, results, batch)
    except Exception:
        return findings
