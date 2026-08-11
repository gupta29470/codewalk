"""Self-upgrade helper for the codewalk MCP install.

Locates the codewalk install from the project's MCP config, pulls the latest
``main`` branch, and reports the new version. The running MCP server must still
be restarted to load the updated code.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_GIT_TIMEOUT_SECONDS = 30.0
_MCP_JSON_CANDIDATES = (".cursor/mcp.json", ".vscode/mcp.json")


def _extract_python(command: str | list[str], args: list[str] | str) -> str | None:
    """Pull the python executable path out of an MCP server command spec."""
    if isinstance(command, list):
        command = " ".join(command)
    command = str(command).strip().strip('"').strip("'")

    name = Path(command).name
    if name.startswith("python"):
        return command

    args_str = " ".join(str(a) for a in args) if isinstance(args, list) else str(args)
    match = re.search(r'["\']?(\S+?)["\']?\s+-m\s+codewalk\.mcp\.server', args_str)
    if match:
        return match.group(1).strip('"').strip("'")
    return None


def _resolve_executable(candidate: str) -> Path | None:
    """Expand ~ and locate the executable if it's not already an absolute path."""
    expanded = Path(candidate).expanduser()
    if expanded.is_absolute() and expanded.exists():
        return expanded.resolve()
    found = shutil.which(candidate)
    if found:
        return Path(found).resolve()
    if expanded.is_absolute():
        return expanded
    return None


def find_codewalk_python_from_mcp_json(project_root: Path) -> Path | None:
    """Read the workspace mcp.json and return the codewalk python executable."""
    for filename in _MCP_JSON_CANDIDATES:
        path = project_root / filename
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.debug("could not parse %s", path)
            continue

        for key in ("servers", "mcpServers"):
            server_cfg: dict[str, Any] | None = data.get(key, {}).get("codewalk")
            if not server_cfg:
                continue
            command = server_cfg.get("command", "")
            args = server_cfg.get("args", [])
            candidate = _extract_python(command, args)
            if candidate:
                resolved = _resolve_executable(candidate)
                if resolved:
                    return resolved
    return None


def _run_git(repo_root: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str] | None:
    """Run a git command in ``repo_root`` and return the result, or None on failure."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=check,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as exc:
        logger.debug("git command failed in %s: %s", repo_root, exc)
        return None


def find_codewalk_package_dir(python_path: Path) -> Path | None:
    """Ask the given python where its installed ``codewalk`` package lives."""
    snippet = "import codewalk, pathlib; print(pathlib.Path(codewalk.__file__).resolve().parent)"
    try:
        result = subprocess.run(
            [str(python_path), "-c", snippet],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as exc:
        logger.debug("could not query codewalk package location with %s: %s", python_path, exc)
        return None
    if result.returncode != 0:
        return None
    package_dir = Path(result.stdout.strip()).resolve()
    return package_dir if package_dir.exists() else None


def _git_root(directory: Path) -> Path | None:
    """Return the git root containing ``directory``."""
    result = _run_git(directory, "rev-parse", "--show-toplevel")
    if result is None or result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def find_codewalk_install_root(project_root: Path) -> Path | None:
    """Locate the codewalk install's git root from the project mcp.json or current process."""
    python_path = find_codewalk_python_from_mcp_json(project_root)
    if python_path:
        package_dir = find_codewalk_package_dir(python_path)
        if package_dir:
            git_root = _git_root(package_dir)
            if git_root:
                return git_root

    try:
        import codewalk

        package_dir = Path(codewalk.__file__).resolve().parent
        git_root = _git_root(package_dir)
        if git_root:
            return git_root
    except Exception as exc:  # pragma: no cover - defensive fallback
        logger.debug("fallback to current process codewalk package failed: %s", exc)

    return None


def is_dirty(repo_root: Path) -> bool:
    """True if the repo has uncommitted changes to tracked files."""
    result = _run_git(repo_root, "status", "--porcelain", "--untracked-files=no")
    if result is None:
        return True
    return result.returncode != 0 or bool(result.stdout.strip())


def current_head(repo_root: Path) -> str | None:
    """Current HEAD sha, or None."""
    result = _run_git(repo_root, "rev-parse", "HEAD")
    if result is None or result.returncode != 0:
        return None
    return result.stdout.strip()


def checkout_and_pull(repo_root: Path) -> tuple[bool, str]:
    """Checkout main and pull origin/main with --ff-only. Returns (ok, output)."""
    checkout = _run_git(repo_root, "checkout", "main")
    if checkout is None or checkout.returncode != 0:
        output = (
            (checkout.stdout + "\n" + checkout.stderr).strip() if checkout else "git checkout failed"
        )
        return False, output

    pull = _run_git(repo_root, "pull", "--ff-only", "origin", "main")
    if pull is None:
        return False, "git pull failed"
    output = (pull.stdout + "\n" + pull.stderr).strip()
    return pull.returncode == 0, output


def count_commits(repo_root: Path, old_head: str | None, new_head: str | None) -> int | None:
    """Count commits between old and new HEAD, or None if not countable."""
    if not old_head or not new_head:
        return None
    if old_head == new_head:
        return 0
    result = _run_git(repo_root, "rev-list", "--count", f"{old_head}..{new_head}")
    if result is None or result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def get_version(codewalk_root: Path) -> str | None:
    """Read the version string from the upgraded codewalk source."""
    init_file = codewalk_root / "src" / "codewalk" / "__init__.py"
    if init_file.exists():
        text = init_file.read_text(encoding="utf-8")
        match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
        if match:
            return match.group(1)

    pyproject = codewalk_root / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        if match:
            return match.group(1)

    return None


def perform_upgrade(project_root: Path) -> str:
    """Pull the latest codewalk main branch and return a status message."""
    codewalk_root = find_codewalk_install_root(project_root)
    if not codewalk_root:
        return "❌ codewalk_upgrade failed: could not locate the codewalk install from mcp.json or the running process."

    if is_dirty(codewalk_root):
        return "❌ codewalk_upgrade failed: codewalk install has uncommitted changes. Commit or stash them first."

    old_head = current_head(codewalk_root)
    ok, output = checkout_and_pull(codewalk_root)
    if not ok:
        return f"❌ codewalk_upgrade failed:\n{output}"

    new_head = current_head(codewalk_root)
    version = get_version(codewalk_root) or "unknown"

    if old_head and new_head and old_head == new_head:
        return f"✅ Codewalk is already up to date (v{version}).\nCurrent HEAD: {new_head[:7]}."

    commits = count_commits(codewalk_root, old_head, new_head)
    commit_msg = f" ({commits} commits pulled)" if commits is not None else ""
    return f"✅ Codewalk upgraded to v{version}{commit_msg}.\nReload/Restart the MCP server to load the updated code."
