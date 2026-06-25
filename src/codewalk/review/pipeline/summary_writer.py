"""Summary writing stage of the review pipeline."""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.codewalk.review.report import Cluster, Verdict

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from src.codewalk.review.report import ReviewReport


def write_summary(
    clusters: list[Cluster],
    verdict: Verdict,
    files_reviewed: int = 0,
) -> str:
    """Write a concise executive summary from ranked clusters.

    This stage is intentionally dumb: it produces prose only and does not
    mutate findings or clusters. Future versions may use an LLM to generate
    more natural prose, but the contract remains the same.

    Args:
        clusters: Ranked clusters.
        verdict: Computed verdict.
        files_reviewed: Number of files reviewed.

    Returns:
        Executive summary string.
    """
    if not clusters:
        if files_reviewed:
            return f"Reviewed {files_reviewed} changed file(s). No issues found."
        return "No issues found."

    by_severity: dict[str, int] = {"blocker": 0, "error": 0, "suggestion": 0}
    for cluster in clusters:
        by_severity[cluster.severity.value] = by_severity.get(cluster.severity.value, 0) + 1

    parts: list[str] = []
    parts.append(f"Reviewed {files_reviewed} changed file(s).")

    if verdict == Verdict.REQUEST_CHANGES:
        parts.append("Merge should not proceed until the following issues are addressed.")
    elif verdict == Verdict.APPROVE_WITH_NITS:
        parts.append("Approving with non-blocking feedback.")
    else:
        parts.append("No blocking issues found.")

    detail_parts: list[str] = []
    if by_severity.get("blocker"):
        detail_parts.append(f"{by_severity['blocker']} blocker")
    if by_severity.get("error"):
        detail_parts.append(f"{by_severity['error']} error")
    if by_severity.get("suggestion"):
        detail_parts.append(f"{by_severity['suggestion']} suggestion")

    if detail_parts:
        parts.append("Found " + ", ".join(detail_parts) + " issue clusters.")

    if clusters:
        top = clusters[0]
        parts.append(f"Top concern: {top.title} ({top.count} occurrence{'s' if top.count != 1 else ''}).")

    return " ".join(parts)


def write_narrative_summary(
    report: "ReviewReport",
    llm: "BaseChatModel",
    cancel_event: "threading.Event | None" = None,
) -> str:
    """Generate an optional LLM-written narrative summary of the review.

    This summary is purely editorial: it may add context and readability but
    must never change the deterministic verdict or findings. If the LLM call
    fails, the deterministic executive summary is returned instead.
    """
    import json
    import threading

    from src.codewalk.review.reviewers.utils import _invoke_with_timeout_and_retry

    issues_block = json.dumps(
        {
            "verdict": report.verdict.value,
            "verdict_reason": report.verdict_reason,
            "executive_summary": report.executive_summary,
            "files_reviewed": report.files_reviewed,
            "lines_added": report.lines_added,
            "lines_removed": report.lines_removed,
            "clusters": [
                {
                    "title": c.title,
                    "severity": c.severity.value,
                    "count": c.count,
                    "file": c.representative_finding.file_path,
                }
                for c in report.clusters[:20]
            ],
        },
        indent=2,
    )

    prompt = (
        "Given the deterministic review output below, write a concise narrative summary (2-4 paragraphs) "
        "explaining the overall health of the change, the most important issues, and any architectural concerns.\n\n"
        f"{issues_block}"
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior staff engineer writing the final summary of a pre-merge code review.\n\n"
                "Rules:\n"
                "- Do not contradict the verdict.\n"
                "- Do not invent findings, file paths, or line numbers that are not in the data.\n"
                "- Do not repeat findings verbatim — summarize patterns and top concerns.\n"
                "- Do not say 'the code looks good overall' when the verdict is request_changes.\n"
                "- Do not change the severity or blocking status of any finding."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    try:
        raw = _invoke_with_timeout_and_retry(
            lambda: llm.invoke(messages),
            operation_name="narrative summary",
            cancel_event=cancel_event,
        )
        content = raw.content if hasattr(raw, "content") else str(raw)
        return content.strip() or report.executive_summary
    except Exception:
        return report.executive_summary
