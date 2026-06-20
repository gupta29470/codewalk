"""Language-aware test runner.

Auto-detects the right test command from repo files and file extensions,
then runs it and returns a normalized result.
"""
from __future__ import annotations

from pathlib import Path

from src.codewalk.log import log as _log
from src.codewalk.tools.tool_runner import get_tool_commands, run_command


class ExecutionResult:
    """Normalized test/lint execution result."""

    def __init__(
        self,
        command: list[str],
        ok: bool,
        returncode: int,
        stdout: str,
        stderr: str,
        error: str | None = None,
    ):
        self.command = command
        self.ok = ok
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.error = error

    def to_dict(self) -> dict:
        return {
            "command": " ".join(self.command),
            "ok": self.ok,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
        }


def _detect_language(file_paths: list[str]) -> str:
    """Detect language from the first file with a known extension."""
    mapping = {
        ".py": "python",
        ".js": "js",
        ".jsx": "js",
        ".ts": "ts",
        ".tsx": "ts",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".kt": "kotlin",
        ".swift": "swift",
        ".php": "php",
        ".rb": "ruby",
        ".cs": "csharp",
        ".dart": "dart",
    }
    for fp in file_paths:
        ext = Path(fp).suffix.lower()
        if ext in mapping:
            return mapping[ext]
    return ""


def _detect_test_command(repo_path: str, language: str) -> list[str] | None:
    """Auto-detect a test command from repo files."""
    repo = Path(repo_path)

    if language == "python":
        if (repo / "pyproject.toml").exists():
            return ["pytest"]
        if (repo / "setup.py").exists() or (repo / "setup.cfg").exists():
            return ["pytest"]
        if any(f.name.startswith("test_") and f.suffix == ".py" for f in repo.rglob("*.py")):
            return ["pytest"]
    elif language in ("js", "ts"):
        if (repo / "package.json").exists():
            return ["npm", "test"]
    elif language == "go":
        return ["go", "test", "./..."]
    elif language == "rust":
        if (repo / "Cargo.toml").exists():
            return ["cargo", "test"]
    elif language == "java":
        if (repo / "pom.xml").exists():
            return ["mvn", "test", "-q"]
        if (repo / "build.gradle").exists() or (repo / "build.gradle.kts").exists():
            return ["gradle", "test"]

    return None


def run_tests(
    repo_path: str,
    file_paths: list[str] | None = None,
    language_hint: str | None = None,
    command: list[str] | None = None,
) -> ExecutionResult:
    """Run tests for the given files/language.

    Args:
        repo_path: Root of the repository.
        file_paths: Files that changed (used for language detection).
        language_hint: Optional language override.
        command: Optional explicit command to run.

    Returns:
        ExecutionResult with stdout/stderr/returncode.
    """
    language = language_hint
    if not language and file_paths:
        language = _detect_language(file_paths)

    # Explicit config override
    configured = get_tool_commands(repo_path, "test_command", language or "")
    if configured and configured[0]:
        cmd = configured[0]
    elif command:
        cmd = command
    else:
        cmd = _detect_test_command(repo_path, language or "")

    if not cmd:
        return ExecutionResult(
            command=[],
            ok=True,
            returncode=0,
            stdout="",
            stderr="",
            error="No test command detected for this repo/language.",
        )

    result = run_command(cmd, cwd=repo_path, timeout=300)
    return ExecutionResult(
        command=cmd,
        ok=result["ok"],
        returncode=result["returncode"],
        stdout=result["stdout"],
        stderr=result["stderr"],
        error=result.get("error"),
    )
