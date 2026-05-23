"""
=============================================================================
 reviewer.py - The Main Code Review Engine
=============================================================================

WHAT THIS FILE DOES:
    Orchestrates the entire code review pipeline:
    1. Get git diff (staged or branch comparison)
    2. Parse diff into structured objects
    3. Run pre-checks (test coverage)
    4. Check blast radius of changed files
    5. Get symbol-level caller context (which functions call the changed code)
    6. Load team guidelines (if configured)
    7. Send to LLM for deep review
    8. Merge all issues into ReviewResult

SYMBOL-LEVEL CALLER CONTEXT (V1.9):
    When graph_store is available, the reviewer looks up callers for ONLY
    the symbols whose line ranges overlap with the diff hunks. This means:
    "You changed authenticate_user() — here are the 5 call sites that
    will be affected." Falls back to file-level importers when graph_store
    is not available.

HOW IT HANDLES LARGE DIFFS:
    - Small diffs (< 200 added lines): single LLM call with all context
    - Large diffs (>= 200 added lines): per-file parallel LLM calls
      using ThreadPoolExecutor (max 4 workers)

WHERE IT'S CALLED:
    - mcp/server.py -> codewalk_review_code() MCP tool
    - api/main.py -> /review endpoint (passes repo_path + ensure_initialized)

DEPENDENCIES:
    - diff_parser.py: get_diff(), get_parsed_diff()
    - models.py: all dataclasses
    - test_coverage.py: pre-check for missing tests
    - guidelines_loader.py: team guidelines
    - review_prompts.py: LLM prompts
    - blast_radius.py: risk assessment
    - graph_store.py: symbol-level caller lookup (optional)
    - config.py: get_llm()

=============================================================================
"""

import json
import asyncio
from pathlib import Path
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor

from src.codewalk.config import get_llm
from src.codewalk.review.diff_parser import get_diff, get_parsed_diff
from src.codewalk.review.models import ReviewResult, Issue, Severity, Category, DiffFile
from src.codewalk.review.test_coverage import TestCoverage
from src.codewalk.review.guidelines_loader import get_guidelines_store, search_guidelines
from src.codewalk.review.review_prompts import REVIEW_SYSTEM_PROMPT, REVIEW_USER_PROMPT

# If total added lines exceed this, switch to per-file chunked review
CHUNK_THRESHOLD = 200


# =============================================================================
# Context Dataclasses
# =============================================================================

@dataclass
class FileReviewContext:
    """Prepared context for reviewing a single file."""
    diff_file: DiffFile
    file_diff_text: str          # Reconstructed unified diff for this file
    file_content: str = ""       # Full file content (for modified files)
    caller_context: str = ""     # Who imports this file
    security_context: str = ""   # Similar patterns from codebase


@dataclass
class ReviewContext:
    """All prepared context needed for a code review."""
    diff_text: str                          # Raw full diff
    diff_files: list[DiffFile]              # Parsed diff files
    file_contexts: list[FileReviewContext]   # Per-file enriched context
    pre_check_issues: list[Issue]           # Issues from TestCoverage
    blast_radius_warnings: list[str]        # High-risk file warnings
    guidelines_context: str                 # Team guidelines text
    total_added: int
    total_removed: int


# =============================================================================
# Helper Functions
# =============================================================================

def _get_file_content(diff_file: DiffFile, repo_path: str | None) -> str:
    """Get full file content for modified files (not new files).

    For new files the diff already has everything.
    For modified files we read the full file so the LLM sees class structure.
    Capped at 500 lines to avoid token overflow.
    """
    if diff_file.is_new_file or not repo_path:
        return ""

    file_path = Path(repo_path) / diff_file.file_path
    if not file_path.exists():
        return ""

    try:
        content = file_path.read_text(errors="replace")
        lines = content.splitlines()
        if len(lines) > 500:
            lines = lines[:500]
            content = "\n".join(lines) + "\n... (truncated at 500 lines)"
        return content
    except (OSError, UnicodeDecodeError):
        return ""


def _get_caller_context(diff_file: DiffFile, deps: dict | None = None, graph_store=None) -> str:
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
    """Query vector store for similar patterns to what's being changed.

    Looks at added code and searches for related patterns in the codebase.
    This gives the LLM context like "here's how auth is handled elsewhere."
    """
    if not store:
        return ""

    from src.codewalk.rag.chain import format_context

    added_code = "\n".join(
        line.content for hunk in diff_file.hunks
        for line in hunk.lines if line.change_type == "added"
    )

    if not added_code:
        return ""

    # Map keywords in code -> targeted search queries
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

    # Search max 2 queries, 2 results each
    all_results = []
    for query in queries[:2]:
        results = store.search(query, n_results=2)
        all_results.extend(results)

    if not all_results:
        return ""

    # Deduplicate by ID
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


# =============================================================================
# prepare_review_context() - Shared Preparation
# =============================================================================

def prepare_review_context(
    staged: bool = False,
    target_branch: str | None = None,
    store=None,
    deps: dict | None = None,
    repo_path: str | None = None,
    graph_store = None,
) -> ReviewContext | None:
    """Common preparation for both MCP and LLM review flows.

    Returns None if the diff is empty (nothing to review).
    """
    # 1. Get raw diff
    diff_text = get_diff(staged=staged, target_branch=target_branch, repo_path=repo_path)
    if not diff_text.strip():
        return None

    # 2. Parse into structured objects
    diff_files = get_parsed_diff(diff_text)
    total_added = sum(df.added_lines for df in diff_files)
    total_removed = sum(df.removed_lines for df in diff_files)

    # 3. Run pre-checks (test coverage)
    pre_check_issues = list(TestCoverage().analyze(diff_files))

    # 4. Check blast radius for high-risk files
    blast_warnings = []
    if deps:
        from src.codewalk.analysis.blast_radius import get_blast_radius
        for df in diff_files:
            radius = get_blast_radius(df.file_path, deps)
            if radius["risk_level"] in ("high", "critical"):
                blast_warnings.append(
                    f"{df.file_path} - {radius['risk_level'].upper()} risk, "
                    f"{radius['affected_files']} dependents"
                )

    # 5. Load team guidelines
    guidelines_context = ""
    guidelines_store = get_guidelines_store()
    if guidelines_store:
        guidelines_context = search_guidelines(guidelines_store, diff_files, n_results=3)

    # 6. Build per-file context
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


# =============================================================================
# LLM Review Functions
# =============================================================================

def _parse_llm_response(content: str, fallback_file: str = "unknown") -> tuple[list[Issue], str]:
    """Parse LLM JSON response into Issue objects. Returns (issues, summary)."""
    # Strip markdown fences if present
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

    issues = []
    for issue in parsed.get("issues", []):
        issues.append(Issue(
            severity=Severity[issue["severity"].upper()],
            category=category_map.get(issue.get("category", "bug"), Category.BUG),
            file_path=issue.get("file", fallback_file),
            line_number=issue.get("line"),
            title=issue.get("title", ""),
            explanation=issue.get("explanation", ""),
            suggestion=issue.get("suggestion"),
            code_snippet=issue.get("code_snippet"),
        ))

    return issues, summary


def _review_single_file(
    diff_file: DiffFile,
    repo_path: str | None,
    store,
    deps: dict | None,
    guidelines_context: str,
    graph_store = None,
) -> list[Issue]:
    """Review ONE file with a focused LLM call."""
    llm = get_llm(temperature=0)

    # Build context sections for this file
    context_parts = []

    file_content = _get_file_content(diff_file, repo_path)
    if file_content:
        context_parts.append(
            f"## Full file content ({diff_file.file_path})\n```\n{file_content}\n```"
        )

    # Caller context
    caller_ctx = _get_caller_context(diff_file, deps, graph_store)
    if caller_ctx:
        context_parts.append(caller_ctx)

    security_ctx = _get_security_context_for_file(diff_file, store)
    if security_ctx:
        context_parts.append(security_ctx)

    if guidelines_context:
        context_parts.append(guidelines_context)

    context_sections = "\n\n".join(context_parts) if context_parts else ""
    system = REVIEW_SYSTEM_PROMPT.format(context_sections=context_sections)

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

    try:
        issues, _ = _parse_llm_response(response.content, diff_file.file_path)
        return issues
    except (json.JSONDecodeError, KeyError, IndexError):
        return []


def _review_all_at_once(
    diff_text: str,
    diff_files: list[DiffFile],
    repo_path: str | None,
    store,
    deps: dict | None,
    pre_check_issues: list[Issue],
) -> tuple[list[Issue], str]:
    """Single-pass review for small diffs (< CHUNK_THRESHOLD lines)."""
    llm = get_llm(temperature=0)

    # Build context
    context_parts = []

    # Blast radius warnings
    if deps:
        from src.codewalk.analysis.blast_radius import get_blast_radius
        high_risk = []
        for df in diff_files:
            radius = get_blast_radius(df.file_path, deps)
            if radius["risk_level"] in ("high", "critical"):
                high_risk.append(
                    f"Warning: {df.file_path} - {radius['risk_level'].upper()} risk, "
                    f"{radius['affected_files']} dependents"
                )
        if high_risk:
            context_parts.append("## Blast Radius Warnings\n" + "\n".join(high_risk))

    # Full file content (first 3 files only to save tokens)
    for df in diff_files[:3]:
        file_content = _get_file_content(df, repo_path)
        if file_content:
            context_parts.append(f"## Full file: {df.file_path}\n```\n{file_content}\n```")

    # Security context from vector store
    if store:
        for df in diff_files[:2]:
            sec_ctx = _get_security_context_for_file(df, store)
            if sec_ctx:
                context_parts.append(sec_ctx)
                break

    # Team guidelines
    guidelines_store = get_guidelines_store()
    if guidelines_store:
        gl = search_guidelines(guidelines_store, diff_files, n_results=3)
        if gl:
            context_parts.append(gl)

    context_sections = "\n\n".join(context_parts) if context_parts else ""
    system = REVIEW_SYSTEM_PROMPT.format(context_sections=context_sections)

    pre_check_str = "\n".join(
        f"- [{issue.severity.value}] {issue.file_path}:{issue.line_number} - {issue.title}"
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

    try:
        return _parse_llm_response(response.content)
    except (json.JSONDecodeError, KeyError, IndexError):
        return [], response.content


# =============================================================================
# review_diff() - Main Entry Point
# =============================================================================

def review_diff(
    staged: bool = False,
    target_branch: str | None = None,
    use_llm: bool = True,
    store=None,
    deps: dict | None = None,
    repo_path: str | None = None,
    graph_store = None,
) -> ReviewResult:
    """Full review pipeline: git diff -> checks -> LLM -> ReviewResult.

    STRATEGY:
        Small diffs (< 200 added lines): single LLM call
        Large diffs (>= 200 added lines): per-file parallel calls (4 workers)

    EXAMPLE TRACE (staged changes: 1 file, 15 lines added):
        ctx = prepare_review_context(staged=True)  → ReviewContext(
            diff_files       = [DiffFile(file_path="color.go", added_lines=15, removed_lines=2)],
            total_added      = 15,   # < CHUNK_THRESHOLD(200) → single-pass review
            pre_check_issues = [Issue(title="No test updates for color.go", severity=WARNING)],
            blast_radius_warnings = ["color.go - HIGH risk, 5 dependents"],
            guidelines_context    = "",
        )
        # total_added(15) < CHUNK_THRESHOLD(200) → _review_all_at_once()
        llm_issues = [Issue(title="Missing nil check", severity=ERROR, line_number=78)]
        all_issues = pre_check_issues + llm_issues  → 2 issues total
        return → ReviewResult(issues=[...], summary="Found 1 bug, 1 test gap")

    Args:
        staged: Review staged changes only (git diff --staged)
        target_branch: Compare against branch (branch...HEAD)
        use_llm: If False, only run pre-checks (no LLM call)
        store: VectorStore for security context search
        deps: Dependency graph for blast radius
        repo_path: Path to the git repo

    Returns:
        ReviewResult with all issues merged
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

    # --- LLM review ---
    llm_issues = []
    llm_summary = ""

    if use_llm:
        if ctx.total_added > CHUNK_THRESHOLD:
            # CHUNKED: per-file parallel review
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
                    f"Warning: {len(errors)} file(s) failed:\n{error_detail}"
                )
            else:
                llm_summary = (
                    f"Reviewed {len(ctx.diff_files)} files individually. "
                    f"Found {len(llm_issues)} issues."
                )
        else:
            # SINGLE PASS: small diff, one LLM call
            llm_issues, llm_summary = _review_all_at_once(
                ctx.diff_text, ctx.diff_files, repo_path, store, deps,
                ctx.pre_check_issues,
            )

    # --- Merge all issues ---
    all_issues = ctx.pre_check_issues + llm_issues

    return ReviewResult(
        issues=all_issues,
        summary=llm_summary or f"Reviewed {len(ctx.diff_files)} files. Found {len(all_issues)} issues.",
        files_reviewed=len(ctx.diff_files),
        lines_added=ctx.total_added,
        lines_removed=ctx.total_removed,
    )
