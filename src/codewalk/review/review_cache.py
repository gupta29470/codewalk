"""Cache for deterministic / expensive review inputs keyed by repo state.

The cache lives under ``<repo_path>/.codewalk/cache/``.  Items are keyed by a
combination of immutable repo state (HEAD SHA + codewalk.yaml mtime) and the
diff parameters used to drive the review.  This makes re-runs on the same tree
instant, while guaranteeing correctness when the tree or the diff target changes.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from src.codewalk.review.diff_parser import ChangedLine, DiffFile, DiffHunk
from src.codewalk.review.static_analysis import StaticAnalysisResult, RiskAnnotation

logger = logging.getLogger("codewalk")


def _cache_dir(repo_path: Path) -> Path:
    path = repo_path / ".codewalk" / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _git_head_sha(repo_path: Path) -> str:
    """Best-effort current HEAD SHA."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _codewalk_yaml_mtime(repo_path: Path) -> str:
    """Modification time of codewalk.yaml, if present."""
    yaml_path = repo_path / "codewalk.yaml"
    try:
        return str(int(yaml_path.stat().st_mtime))
    except Exception:
        return "0"


def get_repo_cache_key(repo_path: Path) -> str:
    """Return the immutable repo-state portion of the cache key."""
    return f"{_git_head_sha(repo_path)}:{_codewalk_yaml_mtime(repo_path)}"


def _build_diff_cache_key(
    repo_cache_key: str,
    target_branch: str | None,
    commit: str | None,
    staged: bool,
    since_commit: str | None,
) -> str:
    """Build a stable cache key that includes the diff target."""
    parts = [
        repo_cache_key,
        f"target={target_branch or 'none'}",
        f"commit={commit or 'none'}",
        f"staged={staged}",
        f"since={since_commit or 'none'}",
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _diff_file_to_dict(df: DiffFile) -> dict[str, Any]:
    return {
        "file_path": df.file_path,
        "language": df.language,
        "hunks": [
            {
                "start_line": h.start_line,
                "end_line": h.end_line,
                "source_start": h.source_start,
                "source_length": h.source_length,
                "lines": [
                    {
                        "line_number": cl.line_number,
                        "content": cl.content,
                        "change_type": cl.change_type,
                    }
                    for cl in h.lines
                ],
            }
            for h in df.hunks
        ],
        "is_new_file": df.is_new_file,
        "is_deleted": df.is_deleted,
        "added_lines": df.added_lines,
        "removed_lines": df.removed_lines,
    }


def _diff_file_from_dict(d: dict[str, Any]) -> DiffFile:
    return DiffFile(
        file_path=d["file_path"],
        language=d.get("language", "python"),
        hunks=[
            DiffHunk(
                start_line=h["start_line"],
                end_line=h["end_line"],
                source_start=h.get("source_start", 0),
                source_length=h.get("source_length", 0),
                lines=[
                    ChangedLine(
                        line_number=cl["line_number"],
                        content=cl["content"],
                        change_type=cl["change_type"],
                    )
                    for cl in h["lines"]
                ],
            )
            for h in d.get("hunks", [])
        ],
        is_new_file=d.get("is_new_file", False),
        is_deleted=d.get("is_deleted", False),
        added_lines=d.get("added_lines", 0),
        removed_lines=d.get("removed_lines", 0),
    )


def _risk_annotation_to_dict(ra: RiskAnnotation) -> dict[str, Any]:
    return {
        "file_path": ra.file_path,
        "risk_score": ra.risk_score,
        "fan_in": ra.fan_in,
        "pagerank": ra.pagerank,
        "cycle_participation": ra.cycle_participation,
        "is_bottleneck": ra.is_bottleneck,
        "affected_files": ra.affected_files,
        "is_high_fan_in": ra.is_high_fan_in,
        "is_high_pagerank": ra.is_high_pagerank,
    }


def _risk_annotation_from_dict(d: dict[str, Any]) -> RiskAnnotation:
    return RiskAnnotation(
        file_path=d["file_path"],
        risk_score=d.get("risk_score", 0.0),
        fan_in=d.get("fan_in", 0),
        pagerank=d.get("pagerank", 0.0),
        cycle_participation=d.get("cycle_participation", False),
        is_bottleneck=d.get("is_bottleneck", False),
        affected_files=d.get("affected_files", []),
        is_high_fan_in=d.get("is_high_fan_in", False),
        is_high_pagerank=d.get("is_high_pagerank", False),
    )


def _static_result_to_dict(static_result: StaticAnalysisResult) -> dict[str, Any]:
    return {
        "diff_files": [_diff_file_to_dict(df) for df in static_result.diff_files],
        "risk_annotations": {
            k: _risk_annotation_to_dict(v)
            for k, v in static_result.risk_annotations.items()
        },
        "total_added": static_result.total_added,
        "total_removed": static_result.total_removed,
    }


def _static_result_from_dict(data: dict[str, Any]) -> StaticAnalysisResult:
    return StaticAnalysisResult(
        diff_files=[_diff_file_from_dict(d) for d in data.get("diff_files", [])],
        risk_annotations={
            k: _risk_annotation_from_dict(v)
            for k, v in data.get("risk_annotations", {}).items()
        },
        total_added=data.get("total_added", 0),
        total_removed=data.get("total_removed", 0),
    )


def load_static_analysis_cache(
    repo_path: Path,
    repo_cache_key: str,
    target_branch: str | None,
    commit: str | None,
    staged: bool,
    since_commit: str | None,
) -> StaticAnalysisResult | None:
    """Load a cached StaticAnalysisResult if one exists for the given state + diff target."""
    key = _build_diff_cache_key(repo_cache_key, target_branch, commit, staged, since_commit)
    path = _cache_dir(repo_path) / f"static_analysis_{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _static_result_from_dict(data)
    except Exception as e:
        logger.warning(f"[review_cache] failed to load static_result cache {path}: {e}")
        return None


def save_static_analysis_cache(
    repo_path: Path,
    repo_cache_key: str,
    target_branch: str | None,
    commit: str | None,
    staged: bool,
    since_commit: str | None,
    static_result: StaticAnalysisResult,
) -> None:
    """Persist a StaticAnalysisResult to disk."""
    key = _build_diff_cache_key(repo_cache_key, target_branch, commit, staged, since_commit)
    path = _cache_dir(repo_path) / f"static_analysis_{key}.json"
    try:
        path.write_text(json.dumps(_static_result_to_dict(static_result), indent=2), encoding="utf-8")
        logger.info(f"[review_cache] saved static_result cache {path}")
    except Exception as e:
        logger.warning(f"[review_cache] failed to save static_result cache {path}: {e}")


def clear_review_cache(repo_path: Path) -> int:
    """Remove all cached review artifacts.  Returns number of files deleted."""
    directory = repo_path / ".codewalk" / "cache"
    if not directory.exists():
        return 0
    count = 0
    for path in directory.glob("static_analysis_*.json"):
        try:
            path.unlink()
            count += 1
        except Exception:
            pass
    return count
