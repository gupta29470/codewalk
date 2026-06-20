"""Tool discovery and command execution helpers."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from src.codewalk.log import log as _log


AVAILABLE_TOOLS_CACHE: dict[str, str | None] = {}


def which(cmd: str) -> str | None:
    """Check if a command is available, with caching."""
    if cmd not in AVAILABLE_TOOLS_CACHE:
        AVAILABLE_TOOLS_CACHE[cmd] = shutil.which(cmd)
    return AVAILABLE_TOOLS_CACHE[cmd]


def clear_tool_cache() -> None:
    """Clear the tool availability cache (useful in tests)."""
    AVAILABLE_TOOLS_CACHE.clear()


def _load_config(repo_path: str) -> dict:
    """Load codewalk.yaml tool overrides if present."""
    try:
        from src.codewalk.team_config import load_codewalk_yaml
        config = load_codewalk_yaml(repo_path)
        return config.tools or {}
    except Exception:
        return {}


def get_tool_commands(repo_path: str, tool_type: str, language: str | None = None) -> list[list[str]] | None:
    """Get configured commands for a tool type and optional language.

    Looks in codewalk.yaml under tools.<tool_type>[.<language>] and splits
    string commands into argument lists.

    Returns None if no override is configured.
    """
    config = _load_config(repo_path)
    section = config.get(tool_type, {})
    if language and isinstance(section, dict):
        commands = section.get(language)
    else:
        commands = section

    if commands is None:
        return None

    if isinstance(commands, str):
        return [commands.split()]
    if isinstance(commands, list):
        parsed = []
        for cmd in commands:
            if isinstance(cmd, str):
                parsed.append(cmd.split())
            elif isinstance(cmd, list):
                parsed.append(cmd)
        return parsed
    return None


def run_command(
    cmd: list[str],
    cwd: str,
    timeout: int = 120,
) -> dict:
    """Run a shell command and return normalized output.

    Returns:
        {
            "ok": bool,
            "returncode": int,
            "stdout": str,
            "stderr": str,
            "error": str | None,
        }
    """
    executable = which(cmd[0])
    if not executable:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "",
            "error": f"Command not found: {cmd[0]}",
        }

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "error": None,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "",
            "error": f"Command timed out after {timeout}s: {' '.join(cmd)}",
        }
    except Exception as e:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": "",
            "error": str(e),
        }
