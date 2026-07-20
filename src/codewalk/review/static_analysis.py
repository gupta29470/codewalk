"""Deterministic static analysis for one-stop review."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.codewalk.review.diff_parser import DiffFile, get_diff, get_parsed_diff


@dataclass
class RiskAnnotation:
    """Pre-computed architecture risk signals for one changed file."""
    file_path: str
    risk_score: float
    fan_in: int = 0
    pagerank: float = 0.0
    cycle_participation: bool = False
    is_bottleneck: bool = False
    affected_files: list[str] = field(default_factory=list)
    is_high_fan_in: bool = False
    is_high_pagerank: bool = False

    def to_prompt_text(self) -> str:
        parts = []
        affected = len(self.affected_files)
        if affected > 0:
            parts.append(f"{affected} affected file(s)")
        if self.is_high_fan_in:
            parts.append(f"{self.fan_in} direct callers")
        if self.is_high_pagerank:
            parts.append(f"PageRank {self.pagerank:.2f}")
        if self.cycle_participation:
            parts.append("in circular dependency")
        if self.is_bottleneck:
            parts.append("architectural bottleneck")
        if not parts:
            return ""
        return f"⚠️ HIGH BLAST RADIUS ({self.file_path}): " + ", ".join(parts) + ". Review with extra care."


@dataclass
class StaticAnalysisResult:
    """Output of the deterministic static analysis layer."""
    diff_files: list[DiffFile] = field(default_factory=list)
    risk_annotations: dict[str, RiskAnnotation] = field(default_factory=dict)
    total_added: int = 0
    total_removed: int = 0


def _extract_changed_lines(diff_file: DiffFile) -> tuple[list[str], list[str]]:
    """Return (old_lines, new_lines) from a parsed diff file."""
    old_lines: list[str] = []
    new_lines: list[str] = []
    for hunk in diff_file.hunks:
        for line in hunk.lines:
            if line.change_type == "removed":
                old_lines.append(line.content)
            elif line.change_type == "added":
                new_lines.append(line.content)
    return old_lines, new_lines


def _compute_percentile_threshold(values: list[float], percentile: float) -> float:
    """Return the value at the given percentile. Empty list returns infinity."""
    if not values:
        return float("inf")
    sorted_values = sorted(values)
    idx = int(len(sorted_values) * percentile / 100)
    return sorted_values[min(idx, len(sorted_values) - 1)]


def _compute_risk_annotations(
    diff_files: list[DiffFile],
    repo_path: Path,
    graph_runtime: Any | None = None,
) -> dict[str, RiskAnnotation]:
    """Compute risk annotations from graph data if available."""
    annotations: dict[str, RiskAnnotation] = {}

    # Pre-compute centrality and cycle data once per review.
    cycle_files: set[str] = set()
    pagerank_by_file: dict[str, float] = {}
    betweenness_by_file: dict[str, float] = {}
    all_pagerank: list[float] = []
    all_betweenness: list[float] = []

    try:
        if graph_runtime is not None and hasattr(graph_runtime, "file_graph"):
            cycle_data = graph_runtime.detect_cycles()
            for group in cycle_data.get("cycle_groups", []):
                cycle_files.update(group)

            names = graph_runtime.file_graph.vs["name"]
            all_pagerank = graph_runtime.file_graph.pagerank()
            all_betweenness = graph_runtime.file_graph.betweenness()
            pagerank_by_file = dict(zip(names, all_pagerank))
            betweenness_by_file = dict(zip(names, all_betweenness))
    except Exception:
        pass

    pagerank_threshold = _compute_percentile_threshold(all_pagerank, 90)
    betweenness_threshold = _compute_percentile_threshold(all_betweenness, 90)

    try:
        from src.codewalk.api.state import get_graph_store
        graph_store = get_graph_store()
    except Exception:
        graph_store = None

    # First pass: collect fan_in / affected_files so thresholds are relative to this diff.
    file_data: list[tuple[DiffFile, int, list[str]]] = []
    for df in diff_files:
        fan_in = 0
        affected_files: list[str] = []

        try:
            if graph_runtime is not None:
                # Graph stores paths relative to the repo root; diff files use
                # the same convention.
                affected_files = graph_runtime.get_blast_radius(df.file_path)
                fan_in = len(affected_files)
        except Exception:
            pass

        # Best-effort direct caller count if graph_store is available.
        try:
            if graph_store and fan_in == 0:
                symbols = graph_store.get_symbols_in_file(df.file_path)
                for sym in symbols:
                    callers = graph_store.get_callers_of_symbol(sym.get("qualified_name", ""))
                    fan_in = max(fan_in, len(callers))
        except Exception:
            pass

        # Fallback: use diff size as a weak proxy
        if fan_in == 0:
            fan_in = min(df.added_lines + df.removed_lines, 50)

        file_data.append((df, fan_in, affected_files))

    fan_in_values = [fan_in for _, fan_in, _ in file_data]
    fan_in_threshold = _compute_percentile_threshold(fan_in_values, 75)

    # Second pass: build annotations using relative thresholds.
    for df, fan_in, affected_files in file_data:
        pagerank = pagerank_by_file.get(df.file_path, min(fan_in / 500.0, 1.0))
        betweenness = betweenness_by_file.get(df.file_path, 0.0)

        score = (
            math.log(fan_in + 1) * 2.0
            + pagerank * 3.0
            + math.log(df.added_lines + df.removed_lines + 1) * 1.5
        )

        annotations[df.file_path] = RiskAnnotation(
            file_path=df.file_path,
            risk_score=score,
            fan_in=fan_in,
            pagerank=pagerank,
            cycle_participation=df.file_path in cycle_files,
            is_bottleneck=betweenness >= betweenness_threshold,
            affected_files=affected_files,
            is_high_fan_in=fan_in > fan_in_threshold,
            is_high_pagerank=pagerank >= pagerank_threshold,
        )

    return annotations


def run_static_analysis(
    repo_path: Path,
    target_branch: str | None,
    commit: str | None = None,
    staged: bool = False,
    graph_runtime: Any | None = None,
    since_commit: str | None = None,
    use_cache: bool = True,
) -> StaticAnalysisResult:
    """Run deterministic static analysis.

    When ``use_cache`` is True and a cached result exists for the current repo
    state + diff target, return it directly.

    Caching is skipped when the diff depends on the mutable working tree
    (default mode, target_branch mode, since_commit mode) because HEAD SHA
    doesn't change when files are edited — only when commits are made.
    Cache is only valid for commit= mode where the diff is immutable.
    """
    # Only cache for immutable diffs (specific commit).
    # Working-tree diffs (default, target_branch, since_commit) are mutable —
    # user can edit files without HEAD changing, making the cache stale.
    cacheable = commit is not None
    if use_cache and cacheable:
        from src.codewalk.review.review_cache import (
            get_repo_cache_key,
            load_static_analysis_cache,
            save_static_analysis_cache,
        )

        repo_cache_key = get_repo_cache_key(repo_path)
        cached = load_static_analysis_cache(
            repo_path,
            repo_cache_key,
            target_branch,
            commit,
            staged,
            since_commit,
        )
        if cached is not None:
            return cached

    diff_text = get_diff(
        repo_path=str(repo_path),
        target_branch=target_branch,
        commit=commit,
        staged=staged,
        since_commit=since_commit,
    )
    diff_files = [
        df for df in get_parsed_diff(diff_text)
        if not df.file_path.startswith(".codewalk/")
    ]

    # Apply codewalk.yaml exclude patterns to diff files
    try:
        from src.codewalk.codewalk_config import load_codewalk_yaml, is_excluded_file
        config = load_codewalk_yaml(str(repo_path))
        diff_files = [
            df for df in diff_files
            if not is_excluded_file(df.file_path, df.file_path, config, repo_path=str(repo_path))
        ]
    except Exception:
        pass  # proceed without filtering if config loading fails

    total_added = 0
    total_removed = 0

    for df in diff_files:
        total_added += df.added_lines
        total_removed += df.removed_lines

    risk_annotations = _compute_risk_annotations(diff_files, repo_path, graph_runtime=graph_runtime)

    result = StaticAnalysisResult(
        diff_files=diff_files,
        risk_annotations=risk_annotations,
        total_added=total_added,
        total_removed=total_removed,
    )

    if use_cache and cacheable:
        save_static_analysis_cache(
            repo_path,
            repo_cache_key,
            target_branch,
            commit,
            staged,
            since_commit,
            result,
        )

    return result
