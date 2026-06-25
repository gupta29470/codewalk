"""Persistent finding store for review history."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.codewalk.review.report import Cluster, Finding, ReviewReport

logger = logging.getLogger("codewalk")


def _reviews_dir(repo_path: Path) -> Path:
    path = repo_path / ".codewalk" / "reviews"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _finding_to_dict(finding: Finding) -> dict[str, Any]:
    return finding.to_dict()


def _finding_from_dict(item: dict[str, Any]) -> Finding:
    from src.codewalk.review.report import (
        Category,
        Confidence,
        Pillar,
        Severity,
        Source,
    )

    return Finding(
        id=item.get("id", ""),
        severity=Severity(item.get("severity", "error")),
        category=Category(item.get("category", "bug")),
        file_path=item.get("file_path", "unknown"),
        line_number=item.get("line_number"),
        title=item.get("title", "Untitled"),
        explanation=item.get("explanation", ""),
        current_code=item.get("current_code"),
        recommended_code=item.get("recommended_code"),
        blocking=item.get("blocking", False),
        confidence=Confidence(item.get("confidence", "medium")),
        source=Source(item.get("source", "llm")),
        pillar=Pillar(item["pillar"]) if item.get("pillar") else None,
        subcategory=item.get("subcategory"),
        evidence=item.get("evidence", []),
        cluster_id=item.get("cluster_id"),
        verifier_notes=item.get("verifier_notes"),
        status=item.get("status", "new"),
    )


def _cluster_from_dict(item: dict[str, Any]) -> Cluster:
    from src.codewalk.review.report import Severity

    return Cluster(
        id=item.get("id", ""),
        title=item.get("title", "Untitled"),
        representative_finding=_finding_from_dict(item["representative_finding"]),
        findings=[_finding_from_dict(f) for f in item.get("findings", [])],
        severity=Severity(item.get("severity", "suggestion")),
        priority=item.get("priority", "P3"),
        count=item.get("count", 0),
        verifier_notes=item.get("verifier_notes"),
    )


@dataclass
class FindingStore:
    """Persisted state of one review run."""

    review_id: str
    commit_sha: str
    parent_review_id: str | None
    branch: str | None
    findings: list[Finding] = field(default_factory=list)
    clusters: list[Cluster] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    # Map of file path -> sha256 of the reviewed content at review time.
    # Used by incremental mode to skip files whose content has not changed.
    file_hashes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "commit_sha": self.commit_sha,
            "parent_review_id": self.parent_review_id,
            "branch": self.branch,
            "findings": [_finding_to_dict(f) for f in self.findings],
            "clusters": [c.to_dict() for c in self.clusters],
            "summary": self.summary,
            "file_hashes": self.file_hashes,
        }


def save_finding_store(repo_path: Path, store: FindingStore) -> None:
    """Persist a FindingStore to disk."""
    directory = _reviews_dir(repo_path)
    path = directory / f"{store.review_id}.json"
    path.write_text(json.dumps(store.to_dict(), indent=2), encoding="utf-8")
    logger.info(f"[finding_store] saved review {store.review_id} to {path}")


def load_finding_store(repo_path: Path, review_id: str) -> FindingStore | None:
    """Load a FindingStore by review ID."""
    path = _reviews_dir(repo_path) / f"{review_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return FindingStore(
            review_id=data["review_id"],
            commit_sha=data.get("commit_sha", ""),
            parent_review_id=data.get("parent_review_id"),
            branch=data.get("branch"),
            findings=[_finding_from_dict(f) for f in data.get("findings", [])],
            clusters=[_cluster_from_dict(c) for c in data.get("clusters", [])],
            summary=data.get("summary", {}),
            file_hashes=data.get("file_hashes", {}),
        )
    except Exception as e:
        logger.warning(f"[finding_store] failed to load {review_id}: {e}")
        return None


def _git_head_sha(repo_path: Path) -> str:
    from src.codewalk.review.utils import git_head_sha
    return git_head_sha(repo_path)


def find_last_review(repo_path: Path, branch: str | None) -> FindingStore | None:
    """Find the most recent persisted review for a branch."""
    directory = _reviews_dir(repo_path)
    if not directory.exists():
        return None

    candidates: list[tuple[Path, FindingStore]] = []
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if branch and data.get("branch") != branch:
                continue
            store = load_finding_store(repo_path, data["review_id"])
            if store:
                candidates.append((path, store))
        except Exception:
            continue

    if not candidates:
        return None

    # Most recently modified file wins.
    candidates.sort(key=lambda x: x[0].stat().st_mtime, reverse=True)
    return candidates[0][1]


def diff_findings(
    current: list[Finding],
    previous: list[Finding],
) -> tuple[list[Finding], list[Finding], list[Finding]]:
    """Compare current findings against previous findings.

    Returns:
        (fixed, still_present, new)
    """
    previous_by_id: dict[str, Finding] = {f.id: f for f in previous}
    current_by_id: dict[str, Finding] = {f.id: f for f in current}

    fixed: list[Finding] = []
    still_present: list[Finding] = []
    new: list[Finding] = []

    for finding in current:
        if finding.id in previous_by_id:
            finding.status = "still_present"
            still_present.append(finding)
        else:
            finding.status = "new"
            new.append(finding)

    for finding in previous:
        if finding.id not in current_by_id:
            finding.status = "fixed"
            fixed.append(finding)

    return fixed, still_present, new


def _file_hash(repo_path: Path, file_path: str) -> str:
    """Return SHA256 hex digest of a file's current content, or '' if missing."""
    import hashlib

    full = repo_path / file_path
    try:
        return hashlib.sha256(full.read_bytes()).hexdigest()
    except Exception:
        return ""


def build_finding_store(
    report: ReviewReport,
    repo_path: Path,
    parent_review_id: str | None = None,
    branch: str | None = None,
    reviewed_file_paths: list[str] | None = None,
) -> FindingStore:
    """Build a FindingStore from a completed ReviewReport.

    Args:
        reviewed_file_paths: All file paths that were reviewed (not just those
            with findings). Used to store content hashes so incremental mode
            can skip unchanged files — even clean ones with zero findings.
    """
    file_hashes: dict[str, str] = {}

    # Hash ALL reviewed files (not just those with findings)
    if reviewed_file_paths:
        for fp in reviewed_file_paths:
            if fp and fp not in file_hashes:
                file_hashes[fp] = _file_hash(repo_path, fp)

    # Also hash any files from findings (in case reviewed_file_paths is incomplete)
    for f in report.findings:
        if f.file_path and f.file_path not in file_hashes:
            file_hashes[f.file_path] = _file_hash(repo_path, f.file_path)

    return FindingStore(
        review_id=report.session_id or "",
        commit_sha=_git_head_sha(repo_path),
        parent_review_id=parent_review_id,
        branch=branch,
        findings=list(report.findings),
        clusters=list(report.clusters),
        summary={
            "verdict": report.verdict.value,
            "verdict_reason": report.verdict_reason,
            "executive_summary": report.executive_summary,
            "files_reviewed": report.files_reviewed,
            "lines_added": report.lines_added,
            "lines_removed": report.lines_removed,
        },
        file_hashes=file_hashes,
    )
