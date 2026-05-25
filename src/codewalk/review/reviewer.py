from __future__ import annotations

import json
import asyncio
from pathlib import Path
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING
from src.codewalk.config import get_llm

if TYPE_CHECKING:
    from src.codewalk.graph.graph_store import GraphStore
from src.codewalk.review.diff_parser import get_diff, get_parsed_diff
from src.codewalk.review.models import ReviewResult, Issue, Severity, Category, DiffFile
from src.codewalk.review.test_coverage import TestCoverage
from src.codewalk.review.guidelines_loader import get_guidelines_store, search_guidelines
from src.codewalk.review.review_prompts import REVIEW_SYSTEM_PROMPT, REVIEW_USER_PROMPT

# Threshold: if total added lines exceed this, use per-file chunked review
CHUNK_THRESHOLD = 200


@dataclass
class FileReviewContext:
    """Prepared context for reviewing a single file."""
    diff_file: DiffFile
    file_diff_text: str
    file_content: str = ""
    caller_context: str = ""
    security_context: str = ""


@dataclass
class ReviewContext:
    """All prepared context needed for a code review (shared by MCP + LLM flows)."""
    diff_text: str
    diff_files: list[DiffFile]
    file_contexts: list[FileReviewContext]
    pre_check_issues: list[Issue]
    blast_radius_warnings: list[str]
    guidelines_context: str
    total_added: int
    total_removed: int


def _get_file_content(diff_file: DiffFile, repo_path: str | None) -> str:
    """Get full file content for modified files (not new files).
    
    For new files: diff already contains everything — return empty.
    For modified files: read the full file so LLM sees class structure.
    """
    if diff_file.is_new_file or not repo_path:
        return ""

    file_path = Path(repo_path) / diff_file.file_path
    if not file_path.exists():
        return ""

    try:
        content = file_path.read_text(errors="replace")
        # Cap at 5000 lines to avoid token overflow
        lines = content.splitlines()
        if len(lines) > 5000:
            lines = lines[:5000]
            content = "\n".join(lines) + "\n... (truncated at 5000 lines)"
        return content
    except (OSError, UnicodeDecodeError):
        return ""


def _get_caller_context(diff_file: DiffFile, deps: dict | None = None,
                        graph_store: GraphStore | None = None) -> str:
    """Symbol-level caller context for code review."""
    if graph_store:
        symbols = graph_store.get_symbols_in_file(diff_file.file_path)
        if symbols:
            changed_lines = set()
            for hunk in diff_file.hunks:
                for line in hunk.lines:
                    if line.change_type in ("added", "removed"):
                        if line.line_number is not None:
                            changed_lines.add(line.line_number)
            
            sections = []
            for symbol in symbols:
                sym_range = set(range(symbol["start_line"], symbol["end_line"] + 1))
                if not changed_lines or changed_lines & sym_range:
                    callers = graph_store.get_callers_of_symbol(symbol["qualified_name"])
                    if callers:
                        caller_lines = []
                        for caller in callers[:15]: # Cap at 15 per symbol
                            caller_lines.append(
                                f"  - {caller['caller']}() at {caller['file']}:{caller['line']}"
                            )
                        sections.append(
                            f"### {symbol['name']}() — called by {len(callers)} symbol(s):\n"
                            + "\n".join(caller_lines)
                        )
            
            if sections:
                return (
                    f"## Caller context for {diff_file.file_path}\n"
                    + "\n\n".join(sections)
                )
            
    if not deps or "graph" not in deps:
        return ""

    from src.codewalk.analysis.blast_radius import build_reverse_graph

    graph = deps["graph"]
    reverse = build_reverse_graph(graph)
    importers = reverse.get(diff_file.file_path, [])

    if not importers:
        return ""

    return (
        f"## Who imports this file\n"
        f"{diff_file.file_path} is imported by: {', '.join(importers[:10])}"
    )


def _get_security_context_for_file(diff_file: DiffFile, store) -> str:
    """Query vector store with security-focused questions for this specific file."""
    if not store:
        return ""

    from src.codewalk.rag.chain import format_context

    added_code = "\n".join(
        line.content for hunk in diff_file.hunks
        for line in hunk.lines if line.change_type == "added"
    )

    if not added_code:
        return ""

    # Build targeted query based on what's in the file
    keywords_to_queries = {
        ("url", "redirect", "launch", "navigate", "href", "link"):
            "URL validation domain allowlist redirect security",
        ("token", "key", "secret", "password", "credential", "auth", "jwt"):
            "authentication token management secure credential storage",
        ("cache", "store", "persist", "save", "memory"):
            "cache eviction memory management cleanup dispose",
        ("timer", "periodic", "stream", "subscription", "controller"):
            "resource disposal cancel timer stream subscription lifecycle",
        ("setstate", "mounted", "dispose", "async"):
            "Flutter async setState mounted check lifecycle",
    }

    queries = []
    lower_code = added_code.lower()
    for keywords, query in keywords_to_queries.items():
        if any(kw in lower_code for kw in keywords):
            queries.append(query)

    if not queries:
        return ""

    all_results = []
    for query in queries[:2]:
        results = store.search(query, n_results=2)
        from src.codewalk.rag.retrieval_quality import filter_by_distance
        filtered, _ = filter_by_distance(results)
        all_results.extend(filtered)

    if not all_results:
        return ""

    # Deduplicate
    seen_ids = set()
    unique_results = []
    for r in all_results:
        if r["id"] not in seen_ids:
            seen_ids.add(r["id"])
            unique_results.append(r)

    return (
        "## How similar patterns are handled elsewhere in this codebase\n"
        + format_context(unique_results[:4])
    )


def _build_file_diff_text(diff_file: DiffFile) -> str:
    """Reconstruct unified diff text for a single file from parsed hunks."""
    lines = []
    lines.append(f"--- a/{diff_file.file_path}")
    lines.append(f"+++ b/{diff_file.file_path}")

    for hunk in diff_file.hunks:
        lines.append(f"@@ -{hunk.start_line},{len(hunk.lines)} @@")
        for line in hunk.lines:
            if line.change_type == "added":
                lines.append(f"+{line.content}")
            elif line.change_type == "removed":
                lines.append(f"-{line.content}")
            else:
                lines.append(f" {line.content}")

    return "\n".join(lines)


def prepare_review_context(
    staged: bool = False,
    target_branch: str | None = None,
    store=None,
    deps: dict | None = None,
    repo_path: str | None = None,
    graph_store = None,
) -> ReviewContext | None:
    """Common preparation for both MCP and LLM review flows.

    Parses diff, runs pre-checks, builds per-file context.
    Returns None if diff is empty.
    """
    # Get diff
    diff_text = get_diff(staged=staged, target_branch=target_branch, repo_path=repo_path)
    if not diff_text.strip():
        return None

    # Parse diff
    diff_files = get_parsed_diff(diff_text)
    total_added = sum(df.added_lines for df in diff_files)
    total_removed = sum(df.removed_lines for df in diff_files)

    # Pre-checks
    pre_check_issues = list(TestCoverage().analyze(diff_files))

    # Blast radius
    blast_warnings = []
    if deps:
        from src.codewalk.analysis.blast_radius import get_blast_radius
        for df in diff_files:
            radius = get_blast_radius(df.file_path, deps)
            if radius["risk_level"] in ("high", "critical"):
                blast_warnings.append(
                    f"{df.file_path} — {radius['risk_level'].upper()} risk, "
                    f"{radius['affected_files']} dependents"
                )

    # Guidelines
    guidelines_context = ""
    guidelines_store = get_guidelines_store()
    if guidelines_store:
        guidelines_context = search_guidelines(guidelines_store, diff_files, n_results=3)

    # Per-file context
    file_contexts = []
    for df in diff_files:
        fc = FileReviewContext(
            diff_file=df,
            file_diff_text=_build_file_diff_text(df),
            file_content=_get_file_content(df, repo_path),
            caller_context=_get_caller_context(df, deps, graph_store),
            security_context=_get_security_context_for_file(df, store),
        )
        file_contexts.append(fc)

    return ReviewContext(
        diff_text=diff_text,
        diff_files=diff_files,
        file_contexts=file_contexts,
        pre_check_issues=pre_check_issues,
        blast_radius_warnings=blast_warnings,
        guidelines_context=guidelines_context,
        total_added=total_added,
        total_removed=total_removed,
    )


def _review_single_file(
    diff_file: DiffFile,
    repo_path: str | None,
    store,
    deps: dict | None,
    guidelines_context: str,
    graph_store = None,
) -> list[Issue]:
    """Review a single file — one focused LLM call."""
    llm = get_llm(temperature=0)

    # Build per-file context
    context_parts = []

    # Full file content for modified files
    file_content = _get_file_content(diff_file, repo_path)
    if file_content:
        context_parts.append(
            f"## Full file content ({diff_file.file_path})\n"
            f"```\n{file_content}\n```"
        )

    # Caller context
    caller_ctx = _get_caller_context(diff_file, deps, graph_store)
    if caller_ctx:
        context_parts.append(caller_ctx)

    # Security context from vector store
    security_ctx = _get_security_context_for_file(diff_file, store)
    if security_ctx:
        context_parts.append(security_ctx)

    # Guidelines
    if guidelines_context:
        context_parts.append(guidelines_context)

    context_sections = "\n\n".join(context_parts) if context_parts else ""

    system = REVIEW_SYSTEM_PROMPT.format(context_sections=context_sections)

    # Build the diff for just this file
    file_diff = _build_file_diff_text(diff_file)

    user = REVIEW_USER_PROMPT.format(
        diff_content=file_diff,
        truncation_notice="",
        pre_checks="(handled separately)",
    )

    response = llm.invoke([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])

    # Parse response
    issues = []
    try:
        content = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        parsed = json.loads(content)

        category_map = {
            "bug": Category.BUG,
            "security": Category.SECURITY,
            "style": Category.STYLE,
        }

        for issue in parsed.get("issues", []):
            issues.append(Issue(
                severity=Severity[issue["severity"].upper()],
                category=category_map.get(
                    issue.get("category", "bug"), Category.BUG
                ),
                file_path=issue.get("file", diff_file.file_path),
                line_number=issue.get("line"),
                title=issue.get("title", ""),
                explanation=issue.get("explanation", ""),
                suggestion=issue.get("suggestion"),
                code_snippet=issue.get("code_snippet"),
            ))
    except (json.JSONDecodeError, KeyError, IndexError):
        pass  # skip unparseable responses for individual files

    return issues


def _review_all_at_once(
    diff_text: str,
    diff_files: list[DiffFile],
    repo_path: str | None,
    store,
    deps: dict | None,
    pre_check_issues: list[Issue],
) -> tuple[list[Issue], str]:
    """Original single-pass review for small diffs (< CHUNK_THRESHOLD lines)."""
    llm = get_llm(temperature=0)

    # Build context
    context_parts = []

    # Blast radius
    if deps:
        from src.codewalk.analysis.blast_radius import get_blast_radius
        high_risk = []
        for df in diff_files:
            radius = get_blast_radius(df.file_path, deps)
            if radius["risk_level"] in ("high", "critical"):
                high_risk.append(
                    f"⚠️ {df.file_path} — {radius['risk_level'].upper()} risk, "
                    f"{radius['affected_files']} dependents"
                )
        if high_risk:
            context_parts.append(
                "## Blast Radius Warnings\n" + "\n".join(high_risk)
            )

    # File content for modified files only
    for df in diff_files[:3]:
        file_content = _get_file_content(df, repo_path)
        if file_content:
            context_parts.append(
                f"## Full file: {df.file_path}\n```\n{file_content}\n```"
            )

    # Security context
    if store:
        for df in diff_files[:2]:
            sec_ctx = _get_security_context_for_file(df, store)
            if sec_ctx:
                context_parts.append(sec_ctx)
                break

    # Guidelines
    guidelines_store = get_guidelines_store()
    if guidelines_store:
        gl = search_guidelines(guidelines_store, diff_files, n_results=3)
        if gl:
            context_parts.append(gl)

    context_sections = "\n\n".join(context_parts) if context_parts else ""

    system = REVIEW_SYSTEM_PROMPT.format(context_sections=context_sections)

    pre_check_str = "\n".join(
        f"- [{issue.severity.value}] {issue.file_path}:{issue.line_number} — {issue.title}"
        for issue in pre_check_issues
    ) or "None found."

    user = REVIEW_USER_PROMPT.format(
        diff_content=diff_text,
        truncation_notice="",
        pre_checks=pre_check_str,
    )

    response = llm.invoke([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])

    issues = []
    summary = ""
    try:
        content = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        parsed = json.loads(content)
        summary = parsed.get("summary", "")

        category_map = {
            "bug": Category.BUG,
            "security": Category.SECURITY,
            "style": Category.STYLE,
        }

        for issue in parsed.get("issues", []):
            issues.append(Issue(
                severity=Severity[issue["severity"].upper()],
                category=category_map.get(
                    issue.get("category", "bug"), Category.BUG
                ),
                file_path=issue.get("file", "unknown"),
                line_number=issue.get("line"),
                title=issue.get("title", ""),
                explanation=issue.get("explanation", ""),
                suggestion=issue.get("suggestion"),
                code_snippet=issue.get("code_snippet"),
            ))
    except (json.JSONDecodeError, KeyError, IndexError):
        summary = response.content

    return issues, summary


def review_diff(
    staged: bool = False,
    target_branch: str | None = None,
    use_llm: bool = True,
    store=None,
    deps: dict | None = None,
    repo_path: str | None = None,
    graph_store = None,
) -> ReviewResult:
    """LLM/API review pipeline: git diff → checks → LLM → ReviewResult.
    
    For small diffs (< 200 added lines): single LLM call with all context.
    For large diffs: per-file parallel LLM calls for focused deep review.
    """
    ctx = prepare_review_context(
        staged=staged,
        target_branch=target_branch,
        store=store,
        deps=deps,
        repo_path=repo_path,
        graph_store=graph_store,
    )

    if ctx is None:
        return ReviewResult(summary="No changes to review.")

    # ── LLM review ──
    llm_issues = []
    llm_summary = ""

    if use_llm:
        if ctx.total_added > CHUNK_THRESHOLD:
            # ─── CHUNKED: Per-file parallel review ───
            errors = []
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [
                    executor.submit(
                        _review_single_file,
                        df, repo_path, store, deps, ctx.guidelines_context,
                        graph_store,
                    )
                    for df in ctx.diff_files
                ]
                for i, future in enumerate(futures):
                    try:
                        file_issues = future.result(timeout=120)
                        llm_issues.extend(file_issues)
                    except Exception as e:
                        errors.append(f"{ctx.diff_files[i].file_path}: {type(e).__name__}: {e}")

            if errors:
                error_detail = "\n".join(errors)
                llm_summary = (
                    f"Reviewed {len(ctx.diff_files)} files individually. "
                    f"Found {len(llm_issues)} issues. "
                    f"⚠️ {len(errors)} file(s) failed:\n{error_detail}"
                )
            else:
                llm_summary = (
                    f"Reviewed {len(ctx.diff_files)} files individually. "
                    f"Found {len(llm_issues)} issues."
                )
        else:
            # ─── SINGLE PASS: Small diff, one call ───
            llm_issues, llm_summary = _review_all_at_once(
                ctx.diff_text, ctx.diff_files, repo_path, store, deps,
                ctx.pre_check_issues,
            )

    # ── Merge and return ──
    all_issues = ctx.pre_check_issues + llm_issues

    return ReviewResult(
        issues=all_issues,
        summary=llm_summary or f"Reviewed {len(ctx.diff_files)} files. "
                                f"Found {len(all_issues)} issues.",
        files_reviewed=len(ctx.diff_files),
        lines_added=ctx.total_added,
        lines_removed=ctx.total_removed,
    )


