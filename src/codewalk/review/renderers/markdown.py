"""Markdown renderer for review reports and context packages."""
from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

from src.codewalk.review.report import (
    ArchitectureFlags,
    Finding,
    ReviewContextPackage,
    ReviewReport,
)


WRAP_WIDTH = 88


def _wrap_text(text: str | None, width: int = WRAP_WIDTH) -> str:
    """Hard-wrap text for readable raw Markdown files."""
    if not text:
        return ""
    # Preserve existing line breaks by wrapping each paragraph independently.
    paragraphs = text.split("\n")
    wrapped = []
    for para in paragraphs:
        if not para.strip():
            wrapped.append("")
        else:
            wrapped.append(textwrap.fill(para, width=width))
    return "\n".join(wrapped)


def _language_for_file(file_path: str) -> str:
    """Guess a code-block language from a file extension."""
    ext = Path(file_path).suffix.lower()
    mapping = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".jsx": "jsx",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".kt": "kotlin",
        ".rb": "ruby",
        ".php": "php",
        ".cpp": "cpp",
        ".c": "c",
        ".h": "c",
        ".cs": "csharp",
        ".swift": "swift",
        ".scala": "scala",
        ".sh": "bash",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".json": "json",
        ".md": "markdown",
        ".sql": "sql",
    }
    return mapping.get(ext, "")


def render_findings_markdown(
    findings: list[dict[str, Any]],
    title: str = "Review Findings",
    source_label: str = "",
) -> str:
    """Render a list of finding dicts as a hard-wrapped Markdown document.

    This is intended as a human-readable companion to the machine-readable
    ``llm_findings.json`` / ``static_findings.json`` files. JSON stays the
    source of truth; Markdown is read-only.
    """
    lines: list[str] = [f"# {title}", ""]
    if source_label:
        lines.extend([f"**Source:** {source_label}", ""])

    if not findings:
        lines.append("_No findings._")
        lines.append("")
        return "\n".join(lines)

    for idx, f in enumerate(findings, start=1):
        severity = f.get("severity", "error")
        category = f.get("category", "")
        subcategory = f.get("subcategory", "")
        file_path = f.get("file_path", "unknown")
        line_number = f.get("line_number")
        title_text = f.get("title", "Untitled")
        confidence = f.get("confidence", "medium")
        source = f.get("source", "")
        finding_id = f.get("id", "")
        blocking = f.get("blocking", False)
        status = f.get("status", "new")
        user_verdict = f.get("user_verdict")
        verifier_notes = f.get("verifier_notes")

        lines.append(f"## {idx}. [{severity}] {title_text}")
        lines.append("")

        meta_parts = []
        if finding_id:
            meta_parts.append(f"**ID:** `{finding_id}`")
        loc = f"`{file_path}"
        if line_number:
            loc += f":{line_number}"
        loc += "`"
        meta_parts.append(f"**File:** {loc}")
        if category:
            cat_label = subcategory if subcategory else category
            meta_parts.append(f"**Category:** {cat_label}")
        if confidence:
            meta_parts.append(f"**Confidence:** {confidence}")
        if source:
            meta_parts.append(f"**Source:** {source}")
        if status and status != "new":
            meta_parts.append(f"**Status:** {status}")
        if blocking:
            meta_parts.append("**Blocking:** true")
        if user_verdict:
            meta_parts.append(f"**Verdict:** {user_verdict}")

        if meta_parts:
            lines.append(" · ".join(meta_parts))
            lines.append("")

        explanation = _wrap_text(f.get("explanation", ""))
        if explanation:
            lines.append(explanation)
            lines.append("")

        current_code = f.get("current_code")
        if current_code:
            lang = _language_for_file(file_path)
            lines.append("### Current code")
            lines.append(f"```{lang}")
            lines.append(current_code.rstrip("\n"))
            lines.append("```")
            lines.append("")

        recommended_code = f.get("recommended_code")
        if recommended_code:
            lang = _language_for_file(file_path)
            lines.append("### Recommended code")
            lines.append(f"```{lang}")
            lines.append(recommended_code.rstrip("\n"))
            lines.append("```")
            lines.append("")

        if verifier_notes:
            lines.append("### Verifier notes")
            lines.append(_wrap_text(verifier_notes))
            lines.append("")

        evidence = f.get("evidence") or []
        if evidence:
            lines.append("### Evidence")
            for ev in evidence:
                if isinstance(ev, dict):
                    ev_text = ev.get("text") or ev.get("summary") or str(ev)
                else:
                    ev_text = str(ev)
                lines.append(f"- {_wrap_text(ev_text)}")
            lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _format_finding(finding: Finding, idx: int) -> list[str]:
    """Format a single finding in the manual-review style."""
    severity_emoji = {
        "blocker": "🔴",
        "error": "🟡",
        "suggestion": "🔵",
    }.get(finding.severity.value, "⚪")
    out: list[str] = [
        f"**{idx}. {severity_emoji} [{finding.severity.value.upper()}]** "
        f"`{finding.file_path}:{finding.line_number or '-'}` — {finding.title}",
        "",
        f"{finding.explanation}",
        "",
    ]
    if finding.current_code:
        out.extend(["**Current:**", "```", finding.current_code, "```", ""])
    if finding.recommended_code:
        out.extend(["**Recommended:**", "```", finding.recommended_code, "```", ""])
    out.append(
        f"blocking: **{'true' if finding.blocking else 'false'}** | "
        f"confidence: {finding.confidence.value}"
    )
    out.append("")
    return out


def _read_changed_file_content(repo_path: Path, file_path: str, max_chars: int = 50000) -> str:
    """Read full content of a changed file, truncating if it exceeds a safe cap."""
    full = repo_path / file_path
    if not full.exists():
        return "(file not found or deleted)"
    try:
        text = full.read_text(encoding="utf-8", errors="replace")
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... ({len(text) - max_chars} characters truncated)"
        return text
    except Exception:
        return "(could not read file)"


def render_review_report(report: ReviewReport) -> str:
    """Render a ReviewReport as markdown."""
    lines: list[str] = [
        f"## Verdict: {report.verdict.value}",
        "",
        f"**Reason:** {report.verdict_reason}",
        "",
        f"**Summary:** {report.executive_summary}",
        "",
    ]

    if report.merge_blockers:
        lines.extend(["### Merge blockers", ""])
        for blocker in report.merge_blockers:
            lines.append(f"- {blocker}")
        lines.append("")

    if report.findings:
        lines.extend(["### Findings", ""])
        for finding in report.findings:
            lines.append(
                f"**{finding.file_path}:{finding.line_number or '-'}** "
                f"[{finding.severity.value}] {finding.title}"
            )
            lines.append(f"{finding.explanation}")
            if finding.current_code:
                lines.extend(["Current:", "```", finding.current_code, "```"])
            if finding.recommended_code:
                lines.extend(["Recommended:", "```", finding.recommended_code, "```"])
            lines.append("")

    return "\n".join(lines)


def render_review_context(package: ReviewContextPackage) -> str:
    """Render a ReviewContextPackage as markdown for the MCP host LLM."""
    lines: list[str] = [
        "# Codewalk Review Context",
        "",
        f"- Repo: `{package.repo_path}`",
        f"- Target branch: `{package.target_branch or 'none'}`",
        f"- Commit: `{package.commit or 'none'}`",
        f"- Files reviewed: {package.files_reviewed} (+{package.lines_added}/-{package.lines_removed})",
    ]
    if package.session_id:
        lines.append(f"- Session ID: `{package.session_id}`")
    if package.folder_name:
        lines.append(f"- Session folder: `{package.folder_name}`")
    if package.current_branch:
        lines.append(f"- Current branch: `{package.current_branch}`")
    lines.append("")

    # ── Persona + instructions ──
    lines.extend([
        "## Instructions",
        "",
        "You are a principal engineer reviewing this PR. You have full codebase context: "
        "file tree, diff, deterministic findings, architecture flags, "
        "and dependency-graph blast radius / centrality / cycle data for every changed file. "
        "Use the risk context to prioritize high-impact issues.",
        "",
        "### What to do",
        "1. Validate each finding below against the diff. Keep real ones, discard false positives.",
        "2. Add any additional issues introduced or worsened by the diff.",
        "3. Weight findings more heavily when a changed file has high blast radius, is a bottleneck, or is in a cycle.",
        "",
        "### Output format",
        "```",
        "## Verdict: <approve | request_changes>",
        "",
        "**Summary:** one paragraph",
        "",
        "### Findings",
        "",
        "**1. 🔴 [CRITICAL]** `file:line` — Title",
        "   Explanation",
        "",
        "   **Current:**",
        "   ```",
        "   old code",
        "   ```",
        "",
        "   **Recommended:**",
        "   ```",
        "   new code",
        "   ```",
        "",
        "   blocking: **true** | confidence: high",
        "```",
        "",
    ])

    # ── Quick signals ──
    flags = package.architecture_flags
    quick: list[str] = []
    if package.deterministic_findings:
        quick.append(f"{len(package.deterministic_findings)} deterministic finding(s)")
    if flags.bottlenecks_touched:
        quick.append(f"{len(flags.bottlenecks_touched)} bottleneck file(s) touched")
    if flags.cycles_touched:
        quick.append(f"{len(flags.cycles_touched)} cycle(s) touched")
    if package.affected_files:
        quick.append(f"{len(package.affected_files)} affected file(s)")
    if quick:
        lines.extend(["### Quick signals", ""])
        for item in quick:
            lines.append(f"- {item}")
        lines.append("")

    # ── Risk context ──
    if package.risk_summary_lines:
        lines.extend(["### Risk context", ""])
        for line in package.risk_summary_lines:
            lines.append(f"- {line}")
        lines.append("")

    # ── Prompt and rubrics ──
    lines.extend(["## Review prompt and rubrics", ""])
    lines.append(package.prompt_core)
    if package.prompt_language:
        lines.extend(["", package.prompt_language])
    if package.prompt_framework:
        lines.extend(["", package.prompt_framework])
    if package.prompt_custom:
        lines.extend(["", package.prompt_custom])
    if package.prompt_fallback:
        lines.extend(["", package.prompt_fallback])
    if package.user_prompt:
        lines.extend(["", "## Team-specific instructions", "", package.user_prompt])
    lines.append("")

    # ── Findings ──
    all_findings = package.findings
    if all_findings:
        lines.extend(["## Findings", ""])
        for idx, finding in enumerate(all_findings, start=1):
            lines.extend(_format_finding(finding, idx))

    # ── Architecture flags ──
    if flags.bottlenecks_touched or flags.cycles_touched:
        lines.extend(["## Architecture flags", ""])
        if flags.bottlenecks_touched:
            lines.append(f"- **Bottlenecks touched:** {', '.join(flags.bottlenecks_touched)}")
        if flags.cycles_touched:
            lines.append(f"- **Cycles touched:** {', '.join(flags.cycles_touched)}")
        lines.append("")

    # ── File tree (capped) ──
    if package.file_tree:
        tree_cap = 150
        lines.extend([
            "## Repository file tree",
            "",
            f"Showing {min(len(package.file_tree), tree_cap)} of {len(package.file_tree)} files.",
            "",
        ])
        for path in package.file_tree[:tree_cap]:
            lines.append(f"- `{path}`")
        if len(package.file_tree) > tree_cap:
            lines.append(f"- ... and {len(package.file_tree) - tree_cap} more files")
        lines.append("")

    # ── Neighborhood context ──
    if package.neighborhood_snippets:
        lines.extend(["## Neighborhood context", ""])
        for snippet in package.neighborhood_snippets:
            name = getattr(snippet, "file_path", "unknown")
            source = getattr(snippet, "source", "context")
            content = getattr(snippet, "content", "")
            lines.extend([f"### {source}: {name}", "```", content, "```", ""])

    # ── Changed files (full content) ──
    if package.diff_files:
        from src.codewalk.review.utils import smart_truncate_file_content

        lines.extend(["## Changed files (full content)", ""])
        for df in package.diff_files:
            lines.append(f"### {df.file_path}")
            content = _read_changed_file_content(package.repo_path, df.file_path)
            if content:
                truncated = smart_truncate_file_content(content, df.hunks, max_tokens=8000)
            else:
                truncated = "(file not found or deleted)"
            lines.append("```")
            lines.append(truncated)
            lines.append("```")
            lines.append("")

    # ── Diff (last) ──
    lines.extend(["## Diff", ""])
    for df in package.diff_files:
        lines.append(f"### {df.file_path} (+{df.added_lines}/-{df.removed_lines})")
        for hunk in df.hunks:
            lines.append(
                f"@@ -{hunk.source_start},{hunk.source_length} +{hunk.start_line},{len(hunk.lines)} @@"
            )
            for line in hunk.lines:
                prefix = {"added": "+", "removed": "-", "context": " "}.get(line.change_type, " ")
                lines.append(f"{prefix}{line.content}")
        lines.append("")

    return "\n".join(lines)
