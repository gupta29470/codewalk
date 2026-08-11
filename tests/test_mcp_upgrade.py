"""Tests for src.codewalk.mcp.upgrade and the codewalk_upgrade MCP tool."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import src.codewalk as codewalk_mod
from src.codewalk.mcp.upgrade import (
    checkout_and_pull,
    count_commits,
    current_head,
    find_codewalk_install_root,
    find_codewalk_python_from_mcp_json,
    get_version,
    is_dirty,
    perform_upgrade,
    _extract_python,
)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=check, timeout=10
    )


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")


def test_extract_python_vs_code_command() -> None:
    assert _extract_python(sys.executable, ["-m", "codewalk.mcp.server"]) == sys.executable


def test_extract_python_cursor_shell() -> None:
    args = [
        "-lc",
        f'cd "${{workspaceFolder}}" && exec {sys.executable} -m codewalk.mcp.server',
    ]
    assert _extract_python("/bin/zsh", args) == sys.executable


def test_extract_python_list_command() -> None:
    assert _extract_python([sys.executable], ["-m", "codewalk.mcp.server"]) == sys.executable


def test_extract_python_quotes_stripped() -> None:
    args = ["-lc", f'exec "{sys.executable}" -m codewalk.mcp.server']
    assert _extract_python("/bin/zsh", args) == sys.executable


def test_extract_python_no_match_returns_none() -> None:
    assert _extract_python("/bin/zsh", ["-lc", "echo hello"]) is None


def test_find_codewalk_python_from_mcp_json_vs_code(tmp_path: Path) -> None:
    mcp_dir = tmp_path / ".vscode"
    mcp_dir.mkdir()
    mcp_json = {
        "servers": {
            "codewalk": {
                "command": sys.executable,
                "args": ["-m", "codewalk.mcp.server"],
                "cwd": "${workspaceFolder}",
            }
        }
    }
    (mcp_dir / "mcp.json").write_text(json.dumps(mcp_json), encoding="utf-8")
    assert find_codewalk_python_from_mcp_json(tmp_path) == Path(sys.executable).resolve()


def test_find_codewalk_python_from_mcp_json_cursor(tmp_path: Path) -> None:
    mcp_dir = tmp_path / ".cursor"
    mcp_dir.mkdir()
    mcp_json = {
        "mcpServers": {
            "codewalk": {
                "command": "/bin/zsh",
                "args": [
                    "-lc",
                    f'cd "${{workspaceFolder}}" && exec {sys.executable} -m codewalk.mcp.server',
                ],
            }
        }
    }
    (mcp_dir / "mcp.json").write_text(json.dumps(mcp_json), encoding="utf-8")
    assert find_codewalk_python_from_mcp_json(tmp_path) == Path(sys.executable).resolve()


def test_find_codewalk_python_from_mcp_json_missing_returns_none(tmp_path: Path) -> None:
    assert find_codewalk_python_from_mcp_json(tmp_path) is None


def test_find_codewalk_install_root_uses_mcp_json(tmp_path: Path) -> None:
    mcp_dir = tmp_path / ".cursor"
    mcp_dir.mkdir()
    mcp_json = {
        "mcpServers": {
            "codewalk": {
                "command": sys.executable,
                "args": ["-m", "codewalk.mcp.server"],
            }
        }
    }
    (mcp_dir / "mcp.json").write_text(json.dumps(mcp_json), encoding="utf-8")
    root = find_codewalk_install_root(tmp_path)
    expected = Path(codewalk_mod.__file__).resolve().parents[2]
    assert root == expected


def test_get_version_from_init(tmp_path: Path) -> None:
    package_dir = tmp_path / "src" / "codewalk"
    package_dir.mkdir(parents=True)
    (package_dir / "__init__.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
    assert get_version(tmp_path) == "9.9.9"


def test_get_version_pyproject_fallback(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "codewalk"\nversion = "8.8.8"\n', encoding="utf-8"
    )
    assert get_version(tmp_path) == "8.8.8"


def test_get_version_missing_returns_none(tmp_path: Path) -> None:
    assert get_version(tmp_path) is None


def test_is_dirty_ignores_untracked(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    (tmp_path / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    assert is_dirty(tmp_path) is False


def test_is_dirty_detects_modified_tracked_file(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("tracked\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    tracked.write_text("modified\n", encoding="utf-8")
    assert is_dirty(tmp_path) is True


def test_perform_upgrade_already_up_to_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.codewalk.mcp.upgrade.find_codewalk_install_root", lambda _x: Path("/fake/codewalk")
    )
    monkeypatch.setattr("src.codewalk.mcp.upgrade.is_dirty", lambda _x: False)
    monkeypatch.setattr("src.codewalk.mcp.upgrade.current_head", lambda _x: "abc1234")
    monkeypatch.setattr(
        "src.codewalk.mcp.upgrade.checkout_and_pull", lambda _x: (True, "Already up to date.")
    )
    monkeypatch.setattr("src.codewalk.mcp.upgrade.get_version", lambda _x: "0.1.2")
    monkeypatch.setattr("src.codewalk.mcp.upgrade.count_commits", lambda _x, _y, _z: 0)

    result = perform_upgrade(Path("/fake/project"))
    assert "already up to date" in result
    assert "v0.1.2" in result


def test_perform_upgrade_pulled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.codewalk.mcp.upgrade.find_codewalk_install_root", lambda _x: Path("/fake/codewalk")
    )
    monkeypatch.setattr("src.codewalk.mcp.upgrade.is_dirty", lambda _x: False)
    heads = ["abc1234", "def5678"]
    monkeypatch.setattr("src.codewalk.mcp.upgrade.current_head", lambda _x: heads.pop(0))
    monkeypatch.setattr(
        "src.codewalk.mcp.upgrade.checkout_and_pull", lambda _x: (True, "Updating abc1234..def5678")
    )
    monkeypatch.setattr("src.codewalk.mcp.upgrade.get_version", lambda _x: "0.1.3")
    monkeypatch.setattr("src.codewalk.mcp.upgrade.count_commits", lambda _x, _y, _z: 3)

    result = perform_upgrade(Path("/fake/project"))
    assert "upgraded to v0.1.3" in result
    assert "3 commits pulled" in result
    assert "Restart" in result


def test_perform_upgrade_dirty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.codewalk.mcp.upgrade.find_codewalk_install_root", lambda _x: Path("/fake/codewalk")
    )
    monkeypatch.setattr("src.codewalk.mcp.upgrade.is_dirty", lambda _x: True)

    result = perform_upgrade(Path("/fake/project"))
    assert result.startswith("❌")
    assert "uncommitted changes" in result


def test_perform_upgrade_pull_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.codewalk.mcp.upgrade.find_codewalk_install_root", lambda _x: Path("/fake/codewalk")
    )
    monkeypatch.setattr("src.codewalk.mcp.upgrade.is_dirty", lambda _x: False)
    monkeypatch.setattr("src.codewalk.mcp.upgrade.current_head", lambda _x: "abc1234")
    monkeypatch.setattr(
        "src.codewalk.mcp.upgrade.checkout_and_pull", lambda _x: (False, "fatal: unable to access")
    )

    result = perform_upgrade(Path("/fake/project"))
    assert result.startswith("❌")
    assert "fatal: unable to access" in result


def test_perform_upgrade_install_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.codewalk.mcp.upgrade.find_codewalk_install_root", lambda _x: None)

    result = perform_upgrade(Path("/fake/project"))
    assert result.startswith("❌")
    assert "could not locate the codewalk install" in result


def test_checkout_and_pull_uses_ff_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_git(repo: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        commands.append(list(args))
        return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")

    monkeypatch.setattr("src.codewalk.mcp.upgrade._run_git", fake_git)
    ok, output = checkout_and_pull(tmp_path)
    assert ok is True
    assert ["checkout", "main"] in commands
    assert ["pull", "--ff-only", "origin", "main"] in commands


def test_count_commits_returns_none_when_head_unknown(tmp_path: Path) -> None:
    assert count_commits(tmp_path, None, "abc") is None
    assert count_commits(tmp_path, "abc", None) is None
