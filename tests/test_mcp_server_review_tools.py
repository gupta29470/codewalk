"""Tests for MCP review tool wrappers in src.codewalk.mcp.server."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.codewalk.mcp import server
from src.codewalk.review.diff_parser import DiffFile
from src.codewalk.review.session import SessionStatus


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test a clean repo-path state."""
    monkeypatch.setattr(server.state, "get_repo_path", lambda: "/fake/repo")
    monkeypatch.setattr(server.state, "ensure_initialized", lambda: True)


def _fake_session(session_id: str = "sess-123", status: SessionStatus = SessionStatus.ACTIVE) -> SimpleNamespace:
    return SimpleNamespace(
        session_id=session_id,
        repo_path="/fake/repo",
        target_branch="main",
        commit=None,
        staged=False,
        status=status,
        folder_name=session_id,
        current_branch="feature",
        created_at="2024-01-01T00:00:00",
        updated_at="2024-01-01T00:00:00",
    )


def test_run_review_no_repo_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server.state, "get_repo_path", lambda: None)
    result = server.codewalk_run_review(target_branch="current")
    assert result.startswith("❌")
    assert "No repository path" in result


def test_run_review_without_target_asks_for_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_require_review_target", lambda _repo, _tb, _staged, _commit: "Which branch?")
    result = server.codewalk_run_review()
    assert "Which branch?" in result


def test_run_review_no_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_require_review_target", lambda _repo, _tb, _staged, _commit: None)
    monkeypatch.setattr(server, "_require_stack", lambda _tool="": None)
    monkeypatch.setattr(
        server, "_start_batched_review", lambda *_a, **_k: (_ for _ in ()).throw(server._NoChangesError())
    )
    result = server.codewalk_run_review(target_branch="current")
    assert "No changes found" in result


def test_run_review_stack_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_require_review_target", lambda _repo, _tb, _staged, _commit: None)
    monkeypatch.setattr(server, "_require_stack", lambda _tool="": None)
    monkeypatch.setattr(
        server,
        "_start_batched_review",
        lambda *_a, **_k: (_ for _ in ()).throw(server._StackRequiredError("main", False, None)),
    )
    result = server.codewalk_run_review(target_branch="main")
    assert "Stack Context Required" in result


def test_run_review_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_require_review_target", lambda _repo, _tb, _staged, _commit: None)
    monkeypatch.setattr(server, "_require_stack", lambda _tool="": None)

    sess = _fake_session("sess-abc")
    batches = [[DiffFile("src/a.py", "python")], [DiffFile("src/b.py", "python")]]
    monkeypatch.setattr(
        server,
        "_start_batched_review",
        lambda *_a, **_k: {
            "session": sess,
            "batches": batches,
            "stack": {"languages": ["python"], "frameworks": ["fastapi"]},
            "diff_files": [DiffFile("src/a.py", "python"), DiffFile("src/b.py", "python")],
            "auto_f": [],
            "first_batch_context": "batch context here",
        },
    )

    result = server.codewalk_run_review(target_branch="main")
    assert "sess-abc" in result
    assert "2 files" in result
    assert "2 batches" in result
    assert "batch context here" in result


def test_re_review_no_previous_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_require_review_target", lambda _repo, _tb, _staged, _commit: None)
    monkeypatch.setattr(
        "src.codewalk.review.session_store.find_last_session", lambda _repo, _branch: None
    )
    result = server.codewalk_re_review(target_branch="main")
    assert result.startswith("❌")
    assert "No previous review session" in result


def test_review_next_batch_unknown_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.codewalk.review.session_store.load_session", lambda _repo, _sid: None)
    result = server.codewalk_review_next_batch("unknown")
    assert result.startswith("❌")
    assert "not found" in result


def test_submit_batch_findings_unknown_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.codewalk.review.session_store.load_session", lambda _repo, _sid: None)
    result = server.codewalk_submit_batch_findings("unknown", [])
    assert result.startswith("❌")
    assert "not found" in result


def test_submit_batch_findings_empty_without_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_id = "sess-submit"
    repo = tmp_path / "repo"
    repo.mkdir()
    session_dir = repo / ".codewalk" / "review_session" / session_id
    session_dir.mkdir(parents=True)
    (session_dir / "llm_findings.json").write_text("[]", encoding="utf-8")

    monkeypatch.setattr(server.state, "get_repo_path", lambda: str(repo))
    monkeypatch.setattr(
        "src.codewalk.review.session_store.load_session",
        lambda _repo, _sid: _fake_session(session_id),
    )
    monkeypatch.setattr(
        "src.codewalk.review.session_store._session_dir",
        lambda _repo, folder: session_dir,
    )

    result = server.codewalk_submit_batch_findings(session_id, [])
    assert result.startswith("⚠️")
    assert "notes" in result


def test_submit_batch_findings_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_id = "sess-submit"
    repo = tmp_path / "repo"
    repo.mkdir()
    session_dir = repo / ".codewalk" / "review_session" / session_id
    session_dir.mkdir(parents=True)
    (session_dir / "llm_findings.json").write_text("[]", encoding="utf-8")
    batch_state = {
        "current_batch_index": 0,
        "batch_queue": [["src/a.py"]],
        "total_batches": 1,
    }
    (session_dir / "batch_state.json").write_text(json.dumps(batch_state), encoding="utf-8")

    monkeypatch.setattr(server.state, "get_repo_path", lambda: str(repo))
    monkeypatch.setattr(
        "src.codewalk.review.session_store.load_session",
        lambda _repo, _sid: _fake_session(session_id),
    )
    monkeypatch.setattr(
        "src.codewalk.review.session_store._session_dir",
        lambda _repo, folder: session_dir,
    )
    monkeypatch.setattr(
        "src.codewalk.review.session_store.set_session_status",
        lambda _session, _status: None,
    )

    finding = {
        "file_path": "src/a.py",
        "line_number": 1,
        "severity": "error",
        "category": "bug",
        "title": "bad",
        "explanation": "expl",
    }
    result = server.codewalk_submit_batch_findings(session_id, [finding])
    assert result.startswith("✅")
    assert "1 findings" in result


def test_get_review_summary_unknown_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.codewalk.review.session_store.load_session", lambda _repo, _sid: None)
    result = server.codewalk_get_review_summary("unknown")
    assert result.startswith("❌")


def test_get_review_details_unknown_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.codewalk.review.session_store.load_session", lambda _repo, _sid: None)
    result = server.codewalk_get_review_details("unknown")
    assert result.startswith("❌")
