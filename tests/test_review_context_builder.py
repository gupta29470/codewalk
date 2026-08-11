"""Tests for the unified batch context builder."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.codewalk.review.context_builder import (
    build_unified_batch_context,
    estimate_shared_context_tokens,
)
from src.codewalk.review.diff_parser import DiffFile, DiffHunk, ChangedLine
from src.codewalk.review.rubric_loader import Rubrics
from src.codewalk.review.static_analysis import StaticAnalysisResult


def test_estimate_shared_context_tokens() -> None:
    assert estimate_shared_context_tokens("") == 0
    assert estimate_shared_context_tokens("abc") == 1
    assert estimate_shared_context_tokens("a" * 300, "b" * 300) == 200


def _df(path: str, is_new: bool = False, added: int = 0, removed: int = 0) -> DiffFile:
    return DiffFile(
        file_path=path,
        language="python",
        hunks=[DiffHunk(1, 1, [ChangedLine(1, "line", "added")])] if not is_new else [],
        is_new_file=is_new,
        added_lines=added,
        removed_lines=removed,
    )


def _build_context(
    monkeypatch: pytest.MonkeyPatch,
    batch: list[DiffFile],
    stack_header: str = "",
    guidelines: str = "",
    user_prompt: str = "",
    include_host_instructions: bool = False,
) -> str:
    monkeypatch.setattr("src.codewalk.review.engine._load_graph_runtime", lambda _rp: (None, False))
    monkeypatch.setattr(
        "src.codewalk.review.context_builder.expand_neighborhood",
        lambda *a, **k: SimpleNamespace(snippets=[]),
    )
    monkeypatch.setattr("src.codewalk.review.context_builder._read_file_content", lambda _rp, _fp: "file content")
    monkeypatch.setattr("src.codewalk.review.context_builder.smart_truncate_file_content", lambda c, _h, **k: c)
    monkeypatch.setattr("src.codewalk.review.context_builder.format_capped_diff", lambda _df, **k: "diff text")
    monkeypatch.setattr("src.codewalk.review.context_builder._git_recent_commits", lambda _rp, _fp: "")
    monkeypatch.setattr("src.codewalk.review.context_builder._format_rubrics", lambda _r: "rubrics text")

    return build_unified_batch_context(
        repo_path=SimpleNamespace(),  # not used because helpers are mocked
        batch=batch,
        static_result=StaticAnalysisResult(),
        stack_header=stack_header,
        rubrics=Rubrics(),
        guidelines=guidelines,
        user_prompt=user_prompt,
        include_host_instructions=include_host_instructions,
    )


def test_build_context_includes_host_instructions(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _build_context(monkeypatch, [_df("src/a.py")], include_host_instructions=True)
    assert "Code Review" in ctx
    assert "Finding fields" in ctx


def test_build_context_includes_stack_header(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _build_context(monkeypatch, [_df("src/a.py")], stack_header="## Stack\npython")
    assert "## Stack" in ctx
    assert "python" in ctx


def test_build_context_includes_guidelines_and_user_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _build_context(
        monkeypatch,
        [_df("src/a.py")],
        guidelines="Use type hints.",
        user_prompt="Focus on security.",
    )
    assert "Use type hints." in ctx
    assert "Focus on security." in ctx


def test_build_context_new_file_omits_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _build_context(monkeypatch, [_df("src/new.py", is_new=True, added=5)])
    assert "new file" in ctx
    assert "Diff:" not in ctx


def test_build_context_modified_file_includes_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _build_context(monkeypatch, [_df("src/mod.py", added=1, removed=1)])
    assert "Diff:" in ctx
    assert "diff text" in ctx
