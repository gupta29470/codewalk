"""Tests for review file grouping and token estimation."""

from __future__ import annotations

import pytest

from src.codewalk.review.diff_parser import ChangedLine, DiffFile, DiffHunk
from src.codewalk.review.engine import (
    _split_batches_by_tokens,
    estimate_file_prompt_tokens,
    group_files_for_review,
)


def _hunk(lines: list[tuple[str, str]]) -> DiffHunk:
    return DiffHunk(
        start_line=1,
        end_line=len(lines),
        lines=[ChangedLine(line_number=i + 1, content=content, change_type=change_type) for i, (change_type, content) in enumerate(lines)],
    )


def _df(path: str, added: int = 0, removed: int = 0, hunks: list[DiffHunk] | None = None, is_new: bool = False) -> DiffFile:
    return DiffFile(
        file_path=path,
        language="python",
        hunks=hunks or [],
        is_new_file=is_new,
        added_lines=added,
        removed_lines=removed,
    )


def test_group_files_empty_returns_empty() -> None:
    assert group_files_for_review([]) == []


def test_group_files_single_batch() -> None:
    files = [_df("src/a.py"), _df("src/b.py")]
    batches = group_files_for_review(files, max_per_batch=5)
    assert len(batches) == 1
    assert [df.file_path for df in batches[0]] == ["src/a.py", "src/b.py"]


def test_group_files_pairs_source_and_test() -> None:
    files = [_df("src/a.py"), _df("tests/test_a.py")]
    batches = group_files_for_review(files, max_per_batch=5)
    assert len(batches) == 1
    assert [df.file_path for df in batches[0]] == ["src/a.py", "tests/test_a.py"]


def test_group_files_respects_max_per_batch() -> None:
    files = [_df(f"src/f{i}.py") for i in range(7)]
    batches = group_files_for_review(files, max_per_batch=3)
    assert len(batches) == 3
    assert sum(len(b) for b in batches) == 7


def test_group_files_preserves_order_within_directory() -> None:
    files = [_df("src/a.py"), _df("src/b.py"), _df("src/c.py")]
    batches = group_files_for_review(files, max_per_batch=2)
    assert [df.file_path for df in batches[0]] == ["src/a.py", "src/b.py"]
    assert [df.file_path for df in batches[1]] == ["src/c.py"]


def test_estimate_file_prompt_tokens_new_file() -> None:
    df = _df("src/new.py", added=10, removed=0, is_new=True)
    tokens = estimate_file_prompt_tokens(df)
    assert tokens > 0


def test_estimate_file_prompt_tokens_modified_includes_hunks() -> None:
    hunks = [_hunk([("added", "x" * 300)])]
    df = _df("src/mod.py", added=1, removed=0, hunks=hunks)
    tokens = estimate_file_prompt_tokens(df)
    assert tokens > 50


def test_split_batches_by_tokens_splits_when_over_budget() -> None:
    big_hunk = _hunk([("added", "x" * 10_000)])
    files = [_df("src/big.py", added=1000, removed=0, hunks=[big_hunk])]
    batches = _split_batches_by_tokens([files], max_tokens_per_batch=1000, base_tokens=0)
    assert len(batches) == 1
    assert batches[0][0].file_path == "src/big.py"


def test_split_batches_by_tokens_groups_small_files() -> None:
    files = [_df(f"src/small{i}.py", added=2, removed=0) for i in range(3)]
    batches = _split_batches_by_tokens([files], max_tokens_per_batch=200_000, base_tokens=0)
    assert len(batches) == 1
    assert len(batches[0]) == 3


def test_group_files_200k_budget_splits_large_batch() -> None:
    big_hunk = _hunk([("added", "x" * 50_000)])
    files = [_df("src/big.py", added=5000, removed=0, hunks=[big_hunk])]
    batches = group_files_for_review(files, max_tokens_per_batch=5_000, base_tokens=0)
    assert all(len(b) == 1 for b in batches)
