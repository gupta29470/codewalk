"""Persistence for internal review sessions.

Sessions are stored under:

    <repo_path>/.codewalk/review_session/<folder_name>/

where <folder_name> is descriptive, e.g.:

    23-June-2026-main
    23-June-2026-feature-x_to_main

Each session folder contains:

    session.json   - full session metadata including a stable session_id
    findings.json  - append-only array of findings (used for batched reviews)
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from src.codewalk.review.report import ReviewContextPackage, ReviewReport
from src.codewalk.review.session import ReviewSession, SessionStatus


REVIEW_SESSION_DIR = Path(".codewalk") / "review_session"


def _session_dir(repo_path: Path, folder_name: str) -> Path:
    return Path(repo_path) / REVIEW_SESSION_DIR / folder_name


def _session_folders(repo_path: Path) -> list[Path]:
    """Return all existing session folders."""
    sessions_root = Path(repo_path) / REVIEW_SESSION_DIR
    if not sessions_root.exists():
        return []
    return [p for p in sessions_root.iterdir() if p.is_dir()]


def _report_from_dict(data: dict[str, Any] | None) -> ReviewReport | None:
    if not data:
        return None
    from src.codewalk.review.report import (
        ArchitectureFlags,
        Category,
        Confidence,
        Finding,
        Severity,
        Source,
        Verdict,
    )

    findings = []
    for item in data.get("issues", []):
        try:
            category = Category(item["category"])
        except ValueError:
            category = Category.BUG
        findings.append(
            Finding(
                severity=Severity(item["severity"]),
                category=category,
                file_path=item["file_path"],
                line_number=item.get("line_number"),
                title=item["title"],
                explanation=item["explanation"],
                current_code=item.get("current_code"),
                recommended_code=item.get("recommended_code"),
                blocking=item.get("blocking", False),
                confidence=Confidence(item.get("confidence", "high")),
                source=Source(item.get("source", "llm")),
                pillar=item.get("pillar"),
                subcategory=item.get("subcategory"),
            )
        )

    arch = data.get("architecture_flags", {})
    return ReviewReport(
        verdict=Verdict(data["verdict"]),
        verdict_reason=data.get("verdict_reason", ""),
        executive_summary=data.get("executive_summary", ""),
        merge_blockers=data.get("merge_blockers", []),
        findings=findings,
        architecture_flags=ArchitectureFlags(
            bottlenecks_touched=arch.get("bottlenecks_touched", []),
            cycles_touched=arch.get("cycles_touched", []),
        ),
        files_reviewed=data.get("files_reviewed", 0),
        lines_added=data.get("lines_added", 0),
        lines_removed=data.get("lines_removed", 0),
        token_usage=data.get("token_usage", 0),
        time_seconds=data.get("time_seconds", 0.0),
        session_id=data.get("session_id", ""),
        folder_name=data.get("folder_name", ""),
    )


def _context_package_from_dict(data: dict[str, Any] | None) -> ReviewContextPackage | None:
    if not data:
        return None
    from src.codewalk.review.report import (
        ArchitectureFlags,
        Category,
        Confidence,
        Finding,
        Severity,
        Source,
    )

    def _findings(items: list[dict[str, Any]]) -> list[Finding]:
        out: list[Finding] = []
        for item in items:
            try:
                category = Category(item.get("category", "bug"))
            except ValueError:
                category = Category.BUG
            out.append(
                Finding(
                    severity=Severity(item.get("severity", "error")),
                    category=category,
                    file_path=item.get("file_path", "unknown"),
                    line_number=item.get("line_number"),
                    title=item.get("title", "Untitled"),
                    explanation=item.get("explanation", ""),
                    current_code=item.get("current_code"),
                    recommended_code=item.get("recommended_code"),
                    blocking=item.get("blocking", False),
                    confidence=Confidence(item.get("confidence", "high")),
                    source=Source(item.get("source", "deterministic")),
                )
            )
        return out

    arch = data.get("architecture_flags", {})

    return ReviewContextPackage(
        repo_path=Path(data.get("repo_path", ".")),
        target_branch=data.get("target_branch"),
        commit=data.get("commit"),
        staged=data.get("staged", False),
        diff_files=[],
        deterministic_findings=_findings(data.get("deterministic_findings", [])),
        neighborhood_snippets=[],
        architecture_flags=ArchitectureFlags(
            bottlenecks_touched=arch.get("bottlenecks_touched", []),
            cycles_touched=arch.get("cycles_touched", []),
        ),
        file_tree=data.get("file_tree", []),
        affected_files=data.get("affected_files", []),
        risk_summary_lines=data.get("risk_summary_lines", []),
        prompt_core=data.get("prompt_core", ""),
        prompt_language=data.get("prompt_language", ""),
        prompt_framework=data.get("prompt_framework", ""),
        prompt_custom=data.get("prompt_custom", ""),
        prompt_fallback=data.get("prompt_fallback", ""),
        user_prompt=data.get("user_prompt", ""),
        session_id=data.get("session_id", ""),
        folder_name=data.get("folder_name", ""),
        current_branch=data.get("current_branch"),
        files_reviewed=data.get("files_reviewed", 0),
        lines_added=data.get("lines_added", 0),
        lines_removed=data.get("lines_removed", 0),
    )


def save_session(session: ReviewSession) -> None:
    """Persist a session to disk (atomic via rename)."""
    folder_name = session.folder_name or session.session_id
    session_dir = _session_dir(Path(session.repo_path), folder_name)
    session_dir.mkdir(parents=True, exist_ok=True)

    session_path = session_dir / "session.json"
    temp = tempfile.NamedTemporaryFile(
        mode="w",
        dir=session_dir,
        delete=False,
        suffix=".tmp",
    )
    data = session.to_dict()
    # Keep session.json small: the full file tree lives in the markdown context
    # package only; it does not need to be persisted in the session record.
    if data.get("context_package"):
        data["context_package"]["file_tree"] = []
    json.dump(data, temp, indent=2)
    temp.close()
    # Atomic rename on POSIX — no window where session_path doesn't exist
    import os
    os.rename(temp.name, str(session_path))

    # Update session index for O(1) lookup
    _update_session_index(Path(session.repo_path), session.session_id, folder_name)


def _session_index_path(repo_path: Path) -> Path:
    return Path(repo_path) / REVIEW_SESSION_DIR / "index.json"


def _update_session_index(repo_path: Path, session_id: str, folder_name: str) -> None:
    """Append session_id → folder_name mapping to index file."""
    index_path = _session_index_path(repo_path)
    index: dict[str, str] = {}
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    index[session_id] = folder_name
    index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")


def load_session(repo_path: Path, session_id: str) -> ReviewSession | None:
    """Load a persisted session from disk by its stable session_id.

    Uses the session index for O(1) lookup. Falls back to linear scan if
    index is missing or stale.
    """
    # Try index first
    index_path = _session_index_path(repo_path)
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
            folder_name = index.get(session_id)
            if folder_name:
                session = load_session_by_folder(repo_path, folder_name)
                if session and session.session_id == session_id:
                    return session
        except Exception:
            pass

    # Fallback: linear scan
    for folder in _session_folders(repo_path):
        session_path = folder / "session.json"
        if not session_path.exists():
            continue
        try:
            data = json.loads(session_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("session_id") != session_id:
            continue

        return ReviewSession(
            session_id=data["session_id"],
            repo_path=data["repo_path"],
            target_branch=data.get("target_branch"),
            commit=data.get("commit"),
            staged=data.get("staged", False),
            status=SessionStatus(data.get("status", "active")),
            report=_report_from_dict(data.get("report")),
            context_package=_context_package_from_dict(data.get("context_package")),
            error=data.get("error"),
            folder_name=data.get("folder_name", folder.name),
            current_branch=data.get("current_branch"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )

    return None


def load_session_by_folder(repo_path: Path, folder_name: str) -> ReviewSession | None:
    """Load a persisted session from disk by its descriptive folder name."""
    session_path = _session_dir(repo_path, folder_name) / "session.json"
    if not session_path.exists():
        return None

    try:
        data = json.loads(session_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    return ReviewSession(
        session_id=data["session_id"],
        repo_path=data["repo_path"],
        target_branch=data.get("target_branch"),
        commit=data.get("commit"),
        staged=data.get("staged", False),
        status=SessionStatus(data.get("status", "active")),
        report=_report_from_dict(data.get("report")),
        context_package=_context_package_from_dict(data.get("context_package")),
        error=data.get("error"),
        folder_name=data.get("folder_name", folder_name),
        current_branch=data.get("current_branch"),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
    )


def save_findings(repo_path: Path, folder_name: str, findings: list[dict[str, Any]]) -> None:
    """Persist or overwrite the findings array for a session."""
    session_dir = _session_dir(repo_path, folder_name)
    session_dir.mkdir(parents=True, exist_ok=True)
    findings_path = session_dir / "findings.json"
    temp = tempfile.NamedTemporaryFile(
        mode="w",
        dir=session_dir,
        delete=False,
        suffix=".tmp",
    )
    json.dump(findings, temp, indent=2)
    temp.close()
    findings_path.write_text(Path(temp.name).read_text(), encoding="utf-8")
    Path(temp.name).unlink()


def load_findings(repo_path: Path, folder_name: str) -> list[dict[str, Any]]:
    """Load the findings array for a session."""
    findings_path = _session_dir(repo_path, folder_name) / "findings.json"
    if not findings_path.exists():
        return []
    try:
        return json.loads(findings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def append_findings(
    repo_path: Path,
    folder_name: str,
    new_findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Append new findings to a session's findings.json and return the merged list."""
    existing = load_findings(repo_path, folder_name)
    existing.extend(new_findings)
    save_findings(repo_path, folder_name, existing)
    return existing


def list_sessions(repo_path: Path) -> list[str]:
    """List descriptive folder names of persisted sessions."""
    return [folder.name for folder in _session_folders(repo_path)]


def delete_session(repo_path: Path, session_id: str) -> bool:
    """Delete a persisted session by session_id."""
    session = load_session(repo_path, session_id)
    if session is None:
        return False
    session_dir = _session_dir(repo_path, session.folder_name or session.session_id)
    if not session_dir.exists():
        return False
    try:
        import shutil
        shutil.rmtree(session_dir)
        return True
    except OSError:
        return False


def save_checkpoint(
    repo_path: Path,
    folder_name: str,
    phase: str,
    findings: list[dict[str, Any]],
) -> None:
    """Persist intermediate findings for a pipeline phase.

    Checkpoints are named ``findings.<phase>.json`` inside the session folder.
    They allow partial recovery and observability without interfering with the
    main ``findings.json`` stream.
    """
    session_dir = _session_dir(repo_path, folder_name)
    session_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = session_dir / f"findings.{phase}.json"
    temp = tempfile.NamedTemporaryFile(
        mode="w",
        dir=session_dir,
        delete=False,
        suffix=".tmp",
    )
    json.dump(findings, temp, indent=2)
    temp.close()
    checkpoint_path.write_text(Path(temp.name).read_text(), encoding="utf-8")
    Path(temp.name).unlink()


def load_checkpoint(
    repo_path: Path,
    folder_name: str,
    phase: str,
) -> list[dict[str, Any]]:
    """Load a persisted checkpoint for a pipeline phase, if it exists."""
    checkpoint_path = _session_dir(repo_path, folder_name) / f"findings.{phase}.json"
    if not checkpoint_path.exists():
        return []
    try:
        return json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
