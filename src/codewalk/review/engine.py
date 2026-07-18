"""One-stop review engine.

Provides two main entry points:
  - `run_review_context()` builds the review context package and returns it to the
    caller without running a review LLM. Used by the MCP `codewalk_run_review` tool.
  - `run_review()` runs the review pipeline (static analysis, neighborhood expansion,
    reviewer batches) and returns raw findings. Used by the API `POST /review`
    endpoint and by `codewalk_review_file`.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.codewalk.review.progress import ReviewProgressReporter

logger = logging.getLogger("codewalk")

from langchain_core.language_models.chat_models import BaseChatModel

from src.codewalk.review.cancellation import (
    ReviewCancelledError,
    check_cancelled,
    end_review,
    start_review,
)
from src.codewalk.review.diff_parser import DiffFile
from src.codewalk.review.static_analysis import StaticAnalysisResult, run_static_analysis
from src.codewalk.config import get_llm as create_review_llm
from src.codewalk.review.neighborhood import (
    NeighborhoodResult,
    expand_neighborhood,
)
from src.codewalk.review.context_builder import (
    _UNIFIED_REVIEW_SYSTEM_PROMPT,
    build_unified_batch_context,
)
from src.codewalk.review.reviewers import ReviewContext
from src.codewalk.review.reviewers.utils import run_structured_review
from src.codewalk.review.report import (
    ArchitectureFlags,
    Category,
    Confidence,
    Finding,
    ReviewContextPackage,
    ReviewReport,
    Severity,
    Source,
)
from src.codewalk.review.rubric_loader import Rubrics, build_rubrics
from src.codewalk.review.session import ReviewSession, SessionStatus
from src.codewalk.review.session_store import (
    load_findings,
    save_findings,
    save_session,
    save_static_findings,
)
from src.codewalk.review.utils import (
    build_session_folder_name,
    get_current_branch,
    get_full_file_tree,
    load_code_guidelines_text,
)
from src.codewalk.codewalk_config import load_codewalk_yaml


@dataclass
class _ReviewInputs:
    """Common inputs assembled for both API and MCP review paths."""

    session: ReviewSession
    static_result: StaticAnalysisResult
    diff_files: list[DiffFile]
    neighborhood: NeighborhoodResult
    static_findings: list[Finding]
    architecture_flags: ArchitectureFlags
    file_tree: list[str]
    code_guidelines_text: str
    user_prompt: str
    rubrics: Rubrics
    affected_files: list[str]
    risk_summary_lines: list[str]
    total_added: int
    total_removed: int


def group_files_for_review(
    diff_files: list[DiffFile],
    risk_annotations: dict[str, Any] | None = None,
    max_per_batch: int = 5,
) -> list[list[DiffFile]]:
    """Group related diff files into review batches.

    Grouping strategy:
      1. Pair source files with their test files
      2. Group same-directory siblings
      3. Sort batches by max risk score (highest first)

    Args:
        diff_files: All changed files to review.
        risk_annotations: Optional risk annotations dict for priority sorting.
        max_per_batch: Maximum files per batch (default 5).

    Returns:
        List of batches, each batch is a list of DiffFiles.
    """
    if not diff_files:
        return []

    used: set[str] = set()
    batches: list[list[DiffFile]] = []
    file_map = {df.file_path: df for df in diff_files}

    def _is_test(path: str) -> bool:
        lower = path.lower()
        return (
            "_test." in lower or ".test." in lower or ".spec." in lower
            or "/test/" in lower or "/tests/" in lower
            or lower.startswith("test/") or lower.startswith("tests/")
            or "/test_" in lower
        )

    def _test_for_source(source_path: str) -> str | None:
        """Find the matching test file for a source file."""
        from pathlib import Path as P
        stem = P(source_path).stem
        # Common patterns: foo.dart → foo_test.dart, foo.py → test_foo.py
        for candidate_path in file_map:
            if candidate_path in used:
                continue
            if not _is_test(candidate_path):
                continue
            candidate_stem = P(candidate_path).stem
            # foo_test matches foo, test_foo matches foo
            if (candidate_stem == f"{stem}_test" or
                candidate_stem == f"test_{stem}" or
                candidate_stem == f"{stem}.test" or
                candidate_stem == f"{stem}_spec"):
                return candidate_path
        return None

    # Sort files: non-test first (so we lead with source, attach test)
    source_files = [df for df in diff_files if not _is_test(df.file_path)]
    test_only_files = [df for df in diff_files if _is_test(df.file_path) and df.file_path not in used]

    # Process source files — pair with tests, group by directory
    dir_groups: dict[str, list[DiffFile]] = {}
    for df in source_files:
        dir_path = str(Path(df.file_path).parent)
        if dir_path not in dir_groups:
            dir_groups[dir_path] = []
        dir_groups[dir_path].append(df)

    for dir_path, files in dir_groups.items():
        current_batch: list[DiffFile] = []

        for df in files:
            if df.file_path in used:
                continue

            current_batch.append(df)
            used.add(df.file_path)

            # Find and attach test file
            test_path = _test_for_source(df.file_path)
            if test_path and test_path not in used:
                current_batch.append(file_map[test_path])
                used.add(test_path)

            # Flush batch if full
            if len(current_batch) >= max_per_batch:
                batches.append(current_batch)
                current_batch = []

        if current_batch:
            batches.append(current_batch)

    # Remaining test files not paired with any source
    orphan_batch: list[DiffFile] = []
    for df in test_only_files:
        if df.file_path in used:
            continue
        orphan_batch.append(df)
        used.add(df.file_path)
        if len(orphan_batch) >= max_per_batch:
            batches.append(orphan_batch)
            orphan_batch = []
    if orphan_batch:
        batches.append(orphan_batch)

    # Sort batches: highest risk first
    def _batch_risk(batch: list[DiffFile]) -> float:
        if not risk_annotations:
            return 0.0
        return max(
            (risk_annotations.get(df.file_path, _NullAnnotation()).risk_score
             for df in batch),
            default=0.0,
        )

    batches.sort(key=_batch_risk, reverse=True)
    return batches


class _NullAnnotation:
    """Stub for missing risk annotations."""
    risk_score: float = 0.0


def _prepare_review_inputs(
    repo_path: Path,
    target_branch: str | None,
    commit: str | None,
    staged: bool,
    codewalk_yaml: Any,
    session: ReviewSession,
    use_cache: bool = True,
    *,
    prebuilt_context: tuple[
        StaticAnalysisResult,
        list[DiffFile],
        NeighborhoodResult,
        list[Finding],
        ArchitectureFlags,
        list[str],
    ] | None = None,
) -> _ReviewInputs:
    """Assemble the common pre-LLM inputs used by both API and MCP review paths."""
    if prebuilt_context is not None:
        static_result, diff_files, neighborhood, static_findings, architecture_flags, file_tree = prebuilt_context
    else:
        static_result, diff_files, neighborhood, static_findings, architecture_flags, file_tree = _build_common_context(
            repo_path,
            target_branch,
            commit,
            staged,
            codewalk_yaml,
            use_cache=use_cache,
        )

    code_guidelines_text = _load_code_guidelines_for_repo(repo_path)

    user_prompt_path = repo_path / ".codewalk" / "review_prompt.md"
    user_prompt = user_prompt_path.read_text(encoding="utf-8") if user_prompt_path.exists() else ""

    relevant_files = {df.file_path for df in diff_files}

    # Stack detection: cached LLM call to identify languages + frameworks
    from src.codewalk.review.stack_detect import (
        detect_stack,
        get_rubric_names_from_stack,
    )
    changed_file_paths = [df.file_path for df in diff_files]
    stack = detect_stack(repo_path, get_full_file_tree(repo_path), changed_file_paths, llm=None)
    rubric_names = get_rubric_names_from_stack(stack)
    rubrics = build_rubrics(repo_path, relevant_files, detected_rubric_names=rubric_names)

    affected_files = sorted(
        {
            path
            for ra in static_result.risk_annotations.values()
            for path in ra.affected_files
        }
    )

    risk_summary_lines: list[str] = []
    for df in diff_files:
        ra = static_result.risk_annotations.get(df.file_path)
        if not ra:
            continue
        parts: list[str] = []
        if ra.affected_files:
            parts.append(f"affects {len(ra.affected_files)} file(s)")
        if ra.is_bottleneck:
            parts.append("architectural bottleneck")
        if ra.cycle_participation:
            parts.append("in circular dependency")
        if ra.pagerank > 0.0:
            parts.append(f"PageRank {ra.pagerank:.4f}")
        if parts:
            risk_summary_lines.append(f"`{df.file_path}`: {', '.join(parts)}")

    total_added = sum(df.added_lines for df in diff_files)
    total_removed = sum(df.removed_lines for df in diff_files)

    return _ReviewInputs(
        session=session,
        static_result=static_result,
        diff_files=diff_files,
        neighborhood=neighborhood,
        static_findings=static_findings,
        architecture_flags=architecture_flags,
        file_tree=file_tree,
        code_guidelines_text=code_guidelines_text,
        user_prompt=user_prompt,
        rubrics=rubrics,
        affected_files=affected_files,
        risk_summary_lines=risk_summary_lines,
        total_added=total_added,
        total_removed=total_removed,
    )


def _build_review_prompt_text(
    repo_path: Path,
    diff_files: list[DiffFile],
    user_prompt: str = "",
    code_guidelines_text: str = "",
    rubrics: Rubrics | None = None,
) -> str:
    """Build the full review prompt in priority order.

    Order:
      1. Code guidelines (from docs, if found)
      2. Core rubric
      3. Language rubric(s) (from file extensions)
      4. Framework rubric
      5. Fallback rubric
      6. User prompt from .codewalk/review_prompt.md
    """
    if rubrics is None:
        rubrics = build_rubrics(repo_path, [df.file_path for df in diff_files])

    language_parts = [rubric for _, rubric in sorted(rubrics.language.items())]
    language = "\n\n".join(language_parts)

    parts: list[str] = []
    if code_guidelines_text:
        parts.append(
            "These code guidelines define this repository's standards. "
            "Enforce them fully, but do not limit the review to only these rules (underfitting) "
            "and do not mechanically pattern-match them (overfitting). "
            "Use your broader engineering judgment to flag any issue introduced or worsened by the diff."
        )
        parts.append(f"## Code guidelines\n\n{code_guidelines_text}")
    if rubrics.core:
        parts.append(rubrics.core)
    if language:
        parts.append(language)
    if rubrics.framework:
        parts.append(rubrics.framework)
    if rubrics.fallback:
        parts.append(rubrics.fallback)
    if user_prompt:
        parts.append(f"## Team-specific instructions\n\n{user_prompt}")

    return "\n\n".join(parts)


def _build_architecture_flags(
    static_result: StaticAnalysisResult,
    relevant_files: set[str] | None = None,
) -> ArchitectureFlags:
    """Build architecture flags from static_result risk annotations."""
    bottlenecks: list[str] = []
    cycles: list[str] = []

    for file_path, annotation in static_result.risk_annotations.items():
        if relevant_files is not None and file_path not in relevant_files:
            continue
        if annotation.is_bottleneck:
            bottlenecks.append(file_path)
        if annotation.cycle_participation:
            cycles.append(file_path)

    return ArchitectureFlags(
        bottlenecks_touched=sorted(set(bottlenecks)),
        cycles_touched=sorted(set(cycles)),
    )


def _build_static_findings(static_result: StaticAnalysisResult) -> list[Finding]:
    """Convert high-impact risk annotations into deterministic (auto) findings."""
    findings: list[Finding] = []
    for file_path, ann in static_result.risk_annotations.items():
        if not (ann.is_high_fan_in or ann.is_high_pagerank or ann.is_bottleneck or ann.cycle_participation):
            continue

        parts: list[str] = []
        if ann.is_high_fan_in:
            parts.append(f"{ann.fan_in} direct callers / affected files")
        if ann.is_high_pagerank:
            parts.append(f"PageRank {ann.pagerank:.4f}")
        if ann.is_bottleneck:
            parts.append("architectural bottleneck")
        if ann.cycle_participation:
            parts.append("circular dependency participant")
        if not parts:
            continue

        explanation = (
            f"`{file_path}` scores highly on dependency-graph risk: "
            + ", ".join(parts)
            + ". Changes in this file can have broad downstream effects; ensure tests and callers are covered."
        )

        findings.append(
            Finding(
                severity=Severity.ERROR if (ann.is_bottleneck or ann.cycle_participation) else Severity.SUGGESTION,
                category=Category.ARCHITECTURE,
                file_path=file_path,
                line_number=None,
                title=f"High-impact file changed: {file_path}",
                explanation=explanation,
                blocking=False,
                confidence=Confidence.HIGH,
                source=Source.DETERMINISTIC,
            )
        )
    return findings


# Keep backward-compatible alias for external callers
_build_layer0_findings = _build_static_findings


def _build_graph_only(repo_path: Path) -> None:
    """Build dependency graph + DuckDB for a repo that has no .codewalk/ index.

    Creates .codewalk/graph.duckdb and .codewalk/knowledge-graph.json.
    Does NOT create ChromaDB, embed chunks, or download any models.
    Takes ~3-7 seconds for a typical repo.
    """
    from src.codewalk.ingestion.scanner import scan_directory
    from src.codewalk.pipeline import build_full_analysis

    try:
        from src.codewalk.codewalk_config import load_codewalk_yaml, codewalk_scan_directory
        config = load_codewalk_yaml(str(repo_path))
        files = codewalk_scan_directory(str(repo_path), config)
    except Exception:
        files = scan_directory(str(repo_path))

    if not files:
        raise ValueError("No indexable files found")

    codewalk_dir = repo_path / ".codewalk"
    codewalk_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(codewalk_dir / "graph.duckdb")

    logger.info(f"[review] Building dependency graph on-the-fly ({len(files)} files)...")
    build_full_analysis(
        db_path=db_path,
        files=files,
        embedded_chunks=None,
        docs_path="",
        repo_path=str(repo_path),
    )
    logger.info(f"[review] Graph ready at {db_path}")


# Public alias so API/MCP can build just the dependency graph + knowledge graph
# without requiring a full ChromaDB embedding index.
build_graph_only = _build_graph_only


def _load_graph_runtime(repo_path: Path) -> tuple[Any | None, bool]:
    """Return a GraphRuntime for the repo and whether we own its store.

    Prefers the API/MCP global state; falls back to the on-disk DuckDB graph.
    If no graph exists, builds one on-the-fly from the repo's source files
    (~3-7s) and persists it to .codewalk/graph.duckdb for future use.
    When loaded from disk, the caller is responsible for closing the store.
    """
    try:
        from src.codewalk.api.state import get_graph_runtime_if_ready

        runtime = get_graph_runtime_if_ready()
        if runtime is not None:
            return runtime, False
    except Exception:
        pass

    db_path = repo_path / ".codewalk" / "graph.duckdb"

    # Build graph on-the-fly if no DuckDB exists
    if not db_path.exists():
        try:
            _build_graph_only(repo_path)
        except Exception as e:
            logger.warning(f"[review] On-the-fly graph build failed: {e}")
            return None, False

    if not db_path.exists():
        return None, False

    try:
        from src.codewalk.graph.graph_runtime import GraphRuntime
        from src.codewalk.graph.graph_store import GraphStore

        store = GraphStore(str(db_path))
        return GraphRuntime(store), True
    except Exception:
        return None, False


def _build_common_context(
    repo_path: Path,
    target_branch: str | None,
    commit: str | None,
    staged: bool,
    codewalk_yaml: Any | None,
    use_cache: bool = True,
) -> tuple[StaticAnalysisResult, list[DiffFile], NeighborhoodResult, list[Finding], ArchitectureFlags, list[str]]:
    """Shared deterministic work used by both MCP and API paths.

    Returns: static_result, diff_files, neighborhood, static_findings,
             architecture_flags, file_tree
    """
    graph_runtime, owns_runtime = _load_graph_runtime(repo_path)
    try:
        static_result = run_static_analysis(
            repo_path=repo_path,
            target_branch=target_branch,
            commit=commit,
            staged=staged,
            graph_runtime=graph_runtime,
            use_cache=use_cache,
        )

        diff_files = static_result.diff_files
        # Pass graph_store to neighborhood so it works in MCP mode too
        graph_store = graph_runtime.store if graph_runtime and hasattr(graph_runtime, "store") else None
        neighborhood = expand_neighborhood(repo_path, diff_files, graph_store=graph_store)
        static_findings = _build_static_findings(static_result)
        file_tree = get_full_file_tree(repo_path)

        relevant_files = {df.file_path for df in diff_files}
        architecture_flags = _build_architecture_flags(static_result, relevant_files)

        return static_result, diff_files, neighborhood, static_findings, architecture_flags, file_tree
    finally:
        if owns_runtime and graph_runtime is not None and hasattr(graph_runtime, "store"):
            try:
                graph_runtime.store.close()
            except Exception:
                pass


def _finding_to_dict(finding: Finding) -> dict[str, Any]:
    return finding.to_dict()


def _category_from_string(value: str | None) -> Category:
    """Map a category string to a Category enum, falling back to bug for unknown values."""
    try:
        return Category(value or "bug")
    except ValueError:
        return Category.BUG


def _finding_from_dict(item: dict[str, Any]) -> Finding:
    finding = Finding(
        severity=Severity(item.get("severity", "error")),
        category=_category_from_string(item.get("category", "bug")),
        file_path=item.get("file_path", "unknown"),
        line_number=item.get("line_number"),
        title=item.get("title", "Untitled"),
        explanation=item.get("explanation", ""),
        current_code=item.get("current_code"),
        recommended_code=item.get("recommended_code"),
        blocking=item.get("blocking", False),
        confidence=Confidence(item.get("confidence", "medium")),
        source=Source(item.get("source", "llm")),
        subcategory=item.get("subcategory"),
        evidence=item.get("evidence", []),
        verifier_notes=item.get("verifier_notes"),
        status=item.get("status", "new"),
        user_verdict=item.get("user_verdict"),
        verdict_at=item.get("verdict_at"),
    )
    # Restore persisted id to maintain stable identity across reloads.
    if item.get("id"):
        finding.id = item["id"]
    return finding


# Per-file token cap used by build_unified_batch_context's smart_truncate_file_content.
_FILE_TOKEN_CAP = 10000


def _estimate_file_tokens(df: DiffFile) -> int:
    """Estimate tokens for a single file in the batch prompt.

    Accounts for the truncation cap applied by build_unified_batch_context and caches
    the result on the DiffFile object for O(1) repeated access.
    """
    cached = getattr(df, "_cached_prompt_tokens", None)
    if cached is not None:
        return cached

    # Diff hunks are always sent in full
    hunk_chars = sum(len(line.content) for hunk in df.hunks for line in hunk.lines)
    hunk_tokens = hunk_chars // 3  # code averages ~3 chars/token

    # File content is truncated to _FILE_TOKEN_CAP by smart_truncate
    file_tokens = min(_FILE_TOKEN_CAP, (df.added_lines + df.removed_lines + 200) * 5)

    total = file_tokens + hunk_tokens + 50  # 50 for headers/formatting
    df._cached_prompt_tokens = total  # type: ignore[attr-defined]
    return total


def _estimate_batch_prompt_tokens(
    repo_path: Path,
    diff_files: list[DiffFile],
    context: ReviewContext,
    reviewer_prompt: str,
) -> int:
    """Estimate total tokens for a batch prompt.

    Uses cached per-file estimates and accounts for the 10000-token truncation
    cap that build_unified_batch_context applies. No disk I/O.
    """
    from src.codewalk.review.utils import count_tokens

    # Base: reviewer prompt + guidelines + user prompt (computed once)
    base = count_tokens(reviewer_prompt) + count_tokens(context.guidelines)
    base += count_tokens(context.user_prompt)

    # File tree (capped at 100 lines in prompt)
    tree_lines = min(len(context.file_tree), 100)
    base += tree_lines * 3  # ~3 tokens per path line

    # Neighborhood context (risk-proportional budget handled elsewhere)
    if context.neighborhood:
        for snippet in context.neighborhood.snippets:
            base += len(snippet.content) // 3

    # Per-file estimate with truncation cap
    file_total = sum(_estimate_file_tokens(df) for df in diff_files)

    return base + file_total


def _make_batches(
    repo_path: Path,
    diff_files: list[DiffFile],
    context: ReviewContext,
    reviewer_prompt: str,
    max_tokens_per_batch: int,
) -> list[list[DiffFile]]:
    """Greedily group diff files into batches that fit the token budget."""
    batches: list[list[DiffFile]] = []
    current_batch: list[DiffFile] = []

    for df in diff_files:
        trial_batch = current_batch + [df]
        tokens = _estimate_batch_prompt_tokens(repo_path, trial_batch, context, reviewer_prompt)
        if current_batch and tokens > max_tokens_per_batch:
            batches.append(current_batch)
            current_batch = [df]
        else:
            current_batch.append(df)

    if current_batch:
        batches.append(current_batch)

    return batches


def _run_review_in_batches(
    repo_path: Path,
    diff_files: list[DiffFile],
    static_result: StaticAnalysisResult,
    llm: BaseChatModel,
    static_findings: list[Finding],
    neighborhood: NeighborhoodResult | None,
    file_tree: list[str],
    code_guidelines_text: str = "",
    user_prompt: str = "",
    rubrics: Rubrics | None = None,
    cancel_event: threading.Event | None = None,
    max_parallel_batches: int = 4,
    max_tokens_per_batch: int = 200_000,
    previous_findings: list[Finding] | None = None,
) -> ReviewReport:
    """Run the review LLM on all diff files using batched, parallel calls.

    Files are grouped into token-budgeted batches. Each batch is reviewed once
    using the unified rubric-based context. Batches run in parallel up to
    ``max_parallel_batches``.
    """
    import os
    from concurrent.futures import ThreadPoolExecutor

    from src.codewalk.review.utils import count_tokens
    from src.codewalk.review.stack_detect import (
        detect_stack,
        format_stack_context_header,
        get_rubric_names_from_stack,
    )

    start_time = time.time()

    relevant_files = {df.file_path for df in diff_files}

    if neighborhood is None:
        neighborhood = expand_neighborhood(repo_path, diff_files)

    # Stack detection: only run if rubrics not pre-provided (avoids duplicate LLM call)
    stack_header = ""
    if rubrics is None:
        changed_file_paths = [df.file_path for df in diff_files]
        stack = detect_stack(repo_path, file_tree, changed_file_paths, llm=llm)
        stack_header = format_stack_context_header(stack)
        rubric_names = get_rubric_names_from_stack(stack)
        rubrics = build_rubrics(repo_path, relevant_files, detected_rubric_names=rubric_names)
    else:
        # Rubrics pre-provided — still get stack header from cache (no LLM call)
        changed_file_paths = [df.file_path for df in diff_files]
        stack = detect_stack(repo_path, file_tree, changed_file_paths, llm=None)
        stack_header = format_stack_context_header(stack)

    # Auto findings (deterministic) tied to current diff files.
    deterministic_findings = [
        f for f in static_findings
        if f.file_path in relevant_files
    ]

    # Sort files by risk so the most impactful files are reviewed first.
    def _risk_score(df: DiffFile) -> float:
        ra = static_result.risk_annotations.get(df.file_path)
        if not ra:
            return 0.0
        return float(ra.risk_score)

    sorted_files = sorted(diff_files, key=_risk_score, reverse=True)

    # Use the unified system prompt for batch sizing.
    reviewer_prompt = _UNIFIED_REVIEW_SYSTEM_PROMPT

    context = ReviewContext(
        repo_path=repo_path,
        file_tree=file_tree,
        guidelines=code_guidelines_text,
        user_prompt=user_prompt,
        prompt_text="",
        rubrics=rubrics,
        neighborhood=neighborhood,
        previous_findings=previous_findings or [],
        cancel_event=cancel_event,
        extra={
            "risk_annotations": static_result.risk_annotations,
            "stack_header": stack_header,
        },
    )

    batches = _make_batches(
        repo_path, sorted_files, context, reviewer_prompt, max_tokens_per_batch
    )

    max_parallel = int(os.getenv("CODEWALK_REVIEW_MAX_PARALLEL_BATCHES", str(max_parallel_batches)))
    total_token_usage = 0

    def _review_batch(batch: list[DiffFile]) -> tuple[list[Finding], int]:
        if cancel_event is not None and cancel_event.is_set():
            raise ReviewCancelledError(getattr(cancel_event, "_review_id", "unknown"))
        batch_context = build_unified_batch_context(
            repo_path=repo_path,
            batch=batch,
            static_result=static_result,
            stack_header=stack_header,
            rubrics=rubrics,
            guidelines=code_guidelines_text,
            user_prompt=user_prompt,
            previous_findings=previous_findings or [],
            cancel_event=cancel_event,
        )
        try:
            return run_structured_review(
                llm,
                batch_context,
                cancel_event=cancel_event,
                system_prompt=_UNIFIED_REVIEW_SYSTEM_PROMPT,
            )
        except Exception as e:
            logger.warning(f"[review] batch failed, continuing with zero findings: {e}")
            return [], 0

    llm_findings: list[Finding] = []
    if len(batches) == 1 or max_parallel <= 1:
        for batch in batches:
            batch_findings, batch_tokens = _review_batch(batch)
            llm_findings.extend(batch_findings)
            total_token_usage += batch_tokens
    else:
        with ThreadPoolExecutor(max_workers=max_parallel) as executor:
            futures = [executor.submit(_review_batch, batch) for batch in batches]
            for future in futures:
                try:
                    batch_findings, batch_tokens = future.result()
                    llm_findings.extend(batch_findings)
                    total_token_usage += batch_tokens
                except ReviewCancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"[review] batch failed, continuing with partial results: {e}")

    architecture_flags = _build_architecture_flags(static_result, relevant_files)

    elapsed = time.time() - start_time

    total_added = sum(df.added_lines for df in diff_files)
    total_removed = sum(df.removed_lines for df in diff_files)

    return ReviewReport(
        findings=llm_findings,
        deterministic_findings=deterministic_findings,
        architecture_flags=architecture_flags,
        files_reviewed=len(diff_files),
        lines_added=total_added,
        lines_removed=total_removed,
        token_usage=total_token_usage,
        time_seconds=elapsed,
    )


def _create_review_session(
    repo_path: Path,
    target_branch: str | None,
    commit: str | None,
    staged: bool,
) -> ReviewSession:
    """Create a ReviewSession with a descriptive folder name."""
    session_id = ReviewSession.generate_id()
    current_branch = get_current_branch(repo_path)
    created_at = datetime.now(timezone.utc)
    folder_name = build_session_folder_name(created_at, current_branch, target_branch)

    return ReviewSession(
        session_id=session_id,
        repo_path=str(repo_path),
        target_branch=target_branch,
        commit=commit,
        staged=staged,
        status=SessionStatus.ACTIVE,
        folder_name=folder_name,
        current_branch=current_branch,
        created_at=created_at.isoformat(),
        updated_at=created_at.isoformat(),
    )


def _load_code_guidelines_for_repo(repo_path: Path) -> str:
    """Load code guidelines from docs_path only.

    Review paths avoid the ChromaDB doc collection to prevent cold-start latency.
    """
    config = load_codewalk_yaml(str(repo_path))
    docs_path = config.docs_path
    code_guidelines = config.code_guidelines
    return load_code_guidelines_text(
        repo_path, docs_path, code_guidelines, use_doc_collection=False
    )


def run_review_context(
    repo_path: Path,
    target_branch: str | None = None,
    commit: str | None = None,
    staged: bool = False,
) -> ReviewContextPackage:
    """Build the same review context the API path uses, but return it to the host LLM (MCP path).

    Runs deterministic checks, then assembles the prompt that the API would send to
    its review LLM. The review LLM (or MCP host LLM) infers the framework from the
    full file tree. Returns that prompt plus supporting data so the host LLM can
    perform the final review.
    """
    session = _create_review_session(repo_path, target_branch, commit, staged)

    try:
        codewalk_yaml = load_codewalk_yaml(str(repo_path))
        inputs = _prepare_review_inputs(
            repo_path, target_branch, commit, staged, codewalk_yaml, session
        )

        if not inputs.diff_files:
            package = ReviewContextPackage(
                repo_path=repo_path,
                target_branch=target_branch,
                commit=commit,
                staged=staged,
                file_tree=inputs.file_tree,
                prompt_core="No diff found to review.",
                session_id=session.session_id,
                folder_name=session.folder_name,
                current_branch=session.current_branch,
            )
            session.status = SessionStatus.COMPLETED
            session.context_package = package
            save_session(session)
            return package

        prompt_text = _build_review_prompt_text(
            repo_path,
            inputs.diff_files,
            user_prompt=inputs.user_prompt,
            code_guidelines_text=inputs.code_guidelines_text,
            rubrics=inputs.rubrics,
        )

        package = ReviewContextPackage(
            repo_path=repo_path,
            target_branch=target_branch,
            commit=commit,
            staged=staged,
            diff_files=inputs.diff_files,
            deterministic_findings=inputs.static_findings,
            neighborhood_snippets=inputs.neighborhood.snippets,
            architecture_flags=inputs.architecture_flags,
            file_tree=inputs.file_tree,
            affected_files=inputs.affected_files,
            risk_summary_lines=inputs.risk_summary_lines,
            prompt_core=prompt_text,
            rubrics=inputs.rubrics,
            session_id=session.session_id,
            folder_name=session.folder_name,
            current_branch=session.current_branch,
            files_reviewed=len(inputs.diff_files),
            lines_added=inputs.total_added,
            lines_removed=inputs.total_removed,
        )
        session.status = SessionStatus.COMPLETED
        session.context_package = package
    except Exception as e:
        session.status = SessionStatus.ERROR
        session.error = str(e)
        package = ReviewContextPackage(
            repo_path=repo_path,
            target_branch=target_branch,
            commit=commit,
            staged=staged,
            file_tree=[],
            prompt_core=f"Review context gathering failed: {e}",
            session_id=session.session_id,
            folder_name=session.folder_name,
            current_branch=session.current_branch,
        )
        session.context_package = package

    save_session(session)
    return package


def run_review(
    repo_path: Path,
    target_branch: str | None = None,
    commit: str | None = None,
    staged: bool = False,
    llm: BaseChatModel | None = None,
    review_id: str | None = None,
    progress_reporter: "ReviewProgressReporter | None" = None,
    file_filter: list[str] | None = None,
    previous_findings: list[Finding] | None = None,
    incremental: bool = False,
    force_full_review: bool = False,
) -> ReviewReport:
    """Run the review LLM on the current diff and return raw findings (API path).

    Args:
        review_id: Optional identifier for this review run. If provided, the
            review can be cancelled via the cancellation API and the engine will
            check for cancellation between phases.
        progress_reporter: Optional reporter used to stream phase progress.
        file_filter: Optional list of file paths to review. When provided, only
            these files are reviewed (others are excluded from the diff). Used
            by codewalk_review_file for single-file deep review.
        previous_findings: Optional findings from an earlier review. When
            provided, the LLM is asked to classify each finding as fixed, new,
            or still present relative to these previous findings.
        incremental: If True, load findings from the most recent session on the
            current branch and pass them as previous_findings.
        force_full_review: If True, ignore incremental/previous findings and run
            a full review.
    """
    from src.codewalk.review.progress import ReviewProgressReporter

    def _report(phase: str, message: str, data: dict | None = None) -> None:
        if progress_reporter is not None:
            progress_reporter.report(phase, message, data)

    cancel_event: threading.Event | None = None
    if review_id:
        start_review(review_id)
        cancel_event = threading.Event()
        # Stash the review id on the event so cancellation errors are informative.
        cancel_event._review_id = review_id  # type: ignore[attr-defined]
    final_report: ReviewReport | None = None
    codewalk_yaml = load_codewalk_yaml(str(repo_path))

    _report("started", "Review started", {"review_id": review_id})

    # Load previous findings for incremental reviews unless caller already supplied them.
    if incremental and not force_full_review and previous_findings is None:
        try:
            from src.codewalk.review.session_store import find_last_session, load_findings

            current_branch = get_current_branch(repo_path)
            last_session = find_last_session(repo_path, current_branch)
            if last_session:
                folder = last_session.folder_name or last_session.session_id
                previous_findings = [
                    _finding_from_dict(f) for f in load_findings(repo_path, folder)
                ]
        except Exception:
            previous_findings = None

    check_cancelled(review_id)
    static_result, diff_files, neighborhood, static_findings, architecture_flags, file_tree = _build_common_context(
        repo_path,
        target_branch,
        commit,
        staged,
        codewalk_yaml,
        use_cache=True,
    )
    _report(
        "static_analysis_complete",
        f"Deterministic analysis complete: {len(diff_files)} diff file(s)",
        {"diff_files": [df.file_path for df in diff_files], "static_findings": len(static_findings)},
    )

    # Apply file_filter: narrow to specific files if requested (single-file review)
    if file_filter:
        filter_set = set(file_filter)
        diff_files = [df for df in diff_files if df.file_path in filter_set]
        static_findings = [f for f in static_findings if f.file_path in filter_set]

    if not diff_files:
        _report("complete", "No diff found to review")
        if review_id:
            end_review(review_id)
        return ReviewReport(
            findings=[],
            deterministic_findings=[],
            files_reviewed=0,
            lines_added=0,
            lines_removed=0,
        )

    llm_instance = llm or create_review_llm(temperature=0)

    session = _create_review_session(repo_path, target_branch, commit, staged)

    # Persist deterministic findings FIRST (same flow as MCP), then run the LLM.
    save_static_findings(
        repo_path,
        session.folder_name,
        [_finding_to_dict(f) for f in static_findings],
    )

    try:
        check_cancelled(review_id)
        inputs = _prepare_review_inputs(
            repo_path,
            target_branch,
            commit,
            staged,
            codewalk_yaml,
            session,
            use_cache=True,
            prebuilt_context=(static_result, diff_files, neighborhood, static_findings, architecture_flags, file_tree),
        )

        check_cancelled(review_id)
        previous_findings_for_llm = previous_findings or []
        batch_report = _run_review_in_batches(
            repo_path=repo_path,
            diff_files=inputs.diff_files,
            static_result=inputs.static_result,
            llm=llm_instance,
            static_findings=inputs.static_findings,
            neighborhood=inputs.neighborhood,
            file_tree=inputs.file_tree,
            code_guidelines_text=inputs.code_guidelines_text,
            user_prompt=inputs.user_prompt,
            rubrics=inputs.rubrics,
            cancel_event=cancel_event,
            previous_findings=previous_findings_for_llm,
        )

        llm_findings = list(batch_report.findings)
        total_token_usage = batch_report.token_usage
        total_time = batch_report.time_seconds
        total_added = batch_report.lines_added
        total_removed = batch_report.lines_removed
        files_reviewed = batch_report.files_reviewed
        _report(
            "batched",
            f"Batch review complete: {len(llm_findings)} raw finding(s) across {files_reviewed} file(s)",
            {"raw_findings": len(llm_findings), "files_reviewed": files_reviewed},
        )

        # Persist LLM findings separately from deterministic findings.
        save_findings(
            repo_path,
            session.folder_name,
            [_finding_to_dict(f) for f in llm_findings],
        )

        final_report = ReviewReport(
            findings=llm_findings,
            deterministic_findings=static_findings,
            architecture_flags=architecture_flags,
            files_reviewed=files_reviewed,
            lines_added=total_added,
            lines_removed=total_removed,
            token_usage=total_token_usage,
            time_seconds=total_time,
            session_id=session.session_id,
            folder_name=session.folder_name,
        )

        session.status = SessionStatus.COMPLETED
        session.report = final_report

        _report(
            "complete",
            "Review complete",
            {
                "llm_findings": len(final_report.findings),
                "static_findings": len(final_report.deterministic_findings),
                "files_reviewed": final_report.files_reviewed,
            },
        )
    except ReviewCancelledError as e:
        session.status = SessionStatus.ERROR
        session.error = str(e)
        final_report = ReviewReport(
            findings=[],
            deterministic_findings=static_findings,
            architecture_flags=architecture_flags,
            session_id=session.session_id,
            folder_name=session.folder_name,
        )
        session.report = final_report
        _report("cancelled", f"Review cancelled: {e}")
    except Exception as e:
        session.status = SessionStatus.ERROR
        session.error = str(e)
        final_report = ReviewReport(
            findings=[],
            deterministic_findings=static_findings,
            architecture_flags=architecture_flags,
            session_id=session.session_id,
            folder_name=session.folder_name,
        )
        session.report = final_report
        _report("error", f"Review failed: {e}")
    finally:
        if review_id:
            end_review(review_id)
        if progress_reporter is not None:
            progress_reporter.done()

    save_session(session)
    return final_report
