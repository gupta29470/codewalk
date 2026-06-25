"""Hard verdict policy for one-stop review."""
from __future__ import annotations

from src.codewalk.review.report import Category, Confidence, Finding, Severity, Verdict


def _is_verdict_blocker(finding: Finding) -> bool:
    """Return True if this finding should block the review by policy."""
    if finding.blocking:
        return True

    if finding.confidence != Confidence.HIGH:
        return False

    # High-confidence security and critical severity findings block by default.
    if finding.category == Category.SECURITY:
        return True
    if finding.severity == Severity.BLOCKER:
        return True

    return False


def _blocker_reason(finding: Finding) -> str | None:
    """Return a human-readable reason if the finding is a blocker, else None."""
    if not _is_verdict_blocker(finding):
        return None

    if finding.blocking:
        return f"blocking {finding.category.value}: {finding.title}"

    if finding.category == Category.SECURITY:
        return f"high-confidence security issue: {finding.title}"

    if finding.severity == Severity.BLOCKER:
        return f"high-confidence critical issue: {finding.title}"

    return f"{finding.category.value}: {finding.title}"


def compute_verdict(findings: list[Finding]) -> tuple[Verdict, str]:
    """Compute verdict deterministically. Returns (verdict, reason)."""
    reasons: list[str] = []

    for finding in findings:
        reason = _blocker_reason(finding)
        if reason:
            reasons.append(reason)

    if reasons:
        return Verdict.REQUEST_CHANGES, "; ".join(reasons)

    if any(f.severity == Severity.ERROR for f in findings):
        return Verdict.APPROVE_WITH_NITS, "warnings present"

    return Verdict.APPROVE, "no issues found"


def compute_merge_blockers(findings: list[Finding]) -> list[str]:
    """Return human-readable merge blocker lines.

    Aligned with compute_verdict: includes any finding that would block the review.
    """
    blockers: list[str] = []
    for finding in findings:
        if not _is_verdict_blocker(finding):
            continue
        loc = f"{finding.file_path}:{finding.line_number}" if finding.line_number else finding.file_path
        blockers.append(f"{loc}: {finding.title}")
    return blockers
