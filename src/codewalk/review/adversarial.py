"""Batched adversarial verification for one-stop review findings."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from src.codewalk.review.report import Category, Confidence, Finding, Severity, Source


VERIFICATION_PROMPT_TEMPLATE = """You are a skeptical senior engineer auditing code review findings.

For each finding:
1. Try to construct a specific scenario where the bug does NOT occur (a counterexample).
2. If you find a valid counterexample and the finding is clearly wrong → verdict: "false_positive".
3. If no counterexample exists AND the evidence is clear in the diff → verdict: "valid".
4. If the evidence is weak, speculative, or the issue is real but low-impact → verdict: "weak" (downgrade confidence, do not drop).

Findings:
<<<FINDINGS_PLACEHOLDER>>>

Return ONLY a JSON array with one object per finding, in the same order:
[
  {
    "finding_id": 1,
    "verdict": "valid" | "false_positive" | "weak",
    "reason": "one sentence",
    "counterexample": "..." // if false_positive
  }
]
"""


@dataclass
class VerificationResult:
    """Outcome of adversarial verification for one finding."""
    finding_id: int
    verdict: str  # "valid" or "false_positive"
    reason: str
    counterexample: str | None = None


@dataclass
class VerificationBatch:
    """A batch of findings selected for verification plus a map back to the original list."""

    findings: list[Finding] = field(default_factory=list)
    # Maps prompt index (1-based) to original index in the input findings list (0-based).
    original_index_map: dict[int, int] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.findings)

    def __bool__(self) -> bool:
        return bool(self.findings)


def _verification_buckets() -> dict[str, int | None]:
    """Return per-bucket verification caps from environment.

    None means "verify all". Defaults favor correctness for high-impact findings
    and sampling for low-impact ones.
    """
    def _parse(value: str | None) -> int | None:
        if value is None:
            return None
        value = value.strip()
        if value.lower() in ("", "all", "none"):
            return None
        return int(value)

    return {
        "critical_blocking": _parse(os.getenv("CODEWALK_VERIFY_CRITICAL_BLOCKING")),
        "security_high": _parse(os.getenv("CODEWALK_VERIFY_SECURITY_HIGH")),
        "warning_high": _parse(os.getenv("CODEWALK_VERIFY_WARNING_HIGH")),
        "warning_medium": _parse(os.getenv("CODEWALK_VERIFY_WARNING_MEDIUM")),
        "suggestion": _parse(os.getenv("CODEWALK_VERIFY_SUGGESTION")),
    }


def _finding_bucket(finding: Finding) -> str:
    """Classify a finding into a verification bucket."""
    if finding.blocking or finding.severity == Severity.BLOCKER:
        return "critical_blocking"
    if finding.category == Category.SECURITY or finding.confidence == Confidence.HIGH:
        return "security_high"
    if finding.severity == Severity.ERROR:
        if finding.confidence == Confidence.MEDIUM:
            return "warning_medium"
        return "warning_high"
    return "suggestion"


def should_verify(finding: Finding) -> bool:
    """Decide whether a finding is worth verifying.

    Verification is the engine's main defense against LLM hallucinations. We
    verify almost everything except:

    - structurally-true deterministic findings (e.g., graph counts)
    - low-priority non-blocking suggestions, to control token cost
    """
    # Deterministic findings are true by construction, unless they can directly
    # block the review. In that case we allow verification to catch stale data.
    if finding.source == Source.DETERMINISTIC:
        return finding.blocking

    # Skip non-blocking suggestions to keep verification cost reasonable.
    if finding.severity == Severity.SUGGESTION and not finding.blocking:
        return False

    return True


def _verification_priority(finding: Finding) -> tuple[int, ...]:
    """Return a sort key where smaller values mean higher priority."""
    return (
        0 if finding.blocking else 1,
        0 if finding.severity == Severity.BLOCKER else 1,
        0 if finding.category == Category.SECURITY else 1,
        0 if finding.severity == Severity.ERROR else 1,
        0 if finding.confidence == Confidence.HIGH else 1,
    )


def prepare_verification_batch(
    findings: list[Finding],
    buckets: dict[str, int | None] | None = None,
) -> VerificationBatch:
    """Select findings for verification using priority buckets.

    Each bucket has its own cap so high-impact findings are always verified while
    lower-impact findings are sampled. The returned batch preserves a map from
    prompt index back to the original findings list so verification results can
    be applied to the correct findings.
    """
    buckets = buckets or _verification_buckets()
    candidates = [(idx, f) for idx, f in enumerate(findings) if should_verify(f)]
    candidates.sort(key=lambda item: _verification_priority(item[1]))

    batch = VerificationBatch()
    counts: dict[str, int] = {k: 0 for k in buckets}

    for original_idx, finding in candidates:
        bucket = _finding_bucket(finding)
        cap = buckets.get(bucket)
        if cap is not None and counts[bucket] >= cap:
            continue

        batch.original_index_map[len(batch.findings) + 1] = original_idx
        batch.findings.append(finding)
        counts[bucket] += 1

    return batch


def build_verification_prompt(batch: VerificationBatch) -> str:
    """Build a batched adversarial prompt."""
    findings_text = "\n\n".join(
        _finding_to_text(i + 1, f) for i, f in enumerate(batch.findings)
    )
    # Use a unique placeholder that cannot appear in code snippets.
    return VERIFICATION_PROMPT_TEMPLATE.replace("<<<FINDINGS_PLACEHOLDER>>>", findings_text)


def _finding_to_text(idx: int, finding: Finding) -> str:
    return (
        f"{idx}. [{finding.severity.value}] {finding.title}\n"
        f"   File: {finding.file_path}:{finding.line_number or '-'}\n"
        f"   Category: {finding.category.value}\n"
        f"   Confidence: {finding.confidence.value}\n"
        f"   Blocking: {finding.blocking}\n"
        f"   Explanation: {finding.explanation}\n"
        f"   Current code: {finding.current_code or '(not provided)'}"
    )


def parse_verification_results(raw_response: str) -> list[VerificationResult]:
    """Parse JSON array of verification results."""
    # Try to extract JSON if wrapped in markdown
    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []

    results: list[VerificationResult] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        results.append(
            VerificationResult(
                finding_id=int(item.get("finding_id", 0)),
                verdict=str(item.get("verdict", "valid")),
                reason=str(item.get("reason", "")),
                counterexample=item.get("counterexample"),
            )
        )

    return results


def apply_verification_results(
    findings: list[Finding],
    results: list[VerificationResult],
    batch: VerificationBatch,
) -> list[Finding]:
    """Drop false positives and downgrade confidence of weak valid findings.

    Uses the batch's original_index_map to apply results to the correct findings
    in the original list.
    """
    fp_ids = {
        r.finding_id
        for r in results
        if r.verdict.lower() == "false_positive"
    }
    weak_ids = {
        r.finding_id
        for r in results
        if r.verdict.lower() == "weak"
    }

    kept: list[Finding] = []
    for original_idx, finding in enumerate(findings):
        # Find the prompt index for this original finding, if any.
        prompt_indices = [
            prompt_idx
            for prompt_idx, orig_idx in batch.original_index_map.items()
            if orig_idx == original_idx
        ]
        if prompt_indices and prompt_indices[0] in fp_ids:
            continue

        matching = [
            r for prompt_idx in prompt_indices
            for r in results if r.finding_id == prompt_idx
        ]
        if matching:
            result = matching[0]
            finding.verifier_notes = result.reason

            # "weak" verdict: keep the finding but downgrade confidence
            if prompt_indices and prompt_indices[0] in weak_ids:
                if finding.confidence != Confidence.LOW:
                    finding.confidence = Confidence.LOW
                finding.blocking = False
            elif finding.confidence == Confidence.MEDIUM:
                reason = result.reason.lower()
                if "possible" in reason or "might" in reason or "unclear" in reason:
                    finding.confidence = Confidence.LOW

        kept.append(finding)

    return kept
