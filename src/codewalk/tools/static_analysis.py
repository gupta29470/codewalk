"""Language-aware static analysis runner.

Discovers and runs linters/type-checkers/security analyzers per language and
normalizes their output into a common issue format.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from src.codewalk.log import log as _log
from src.codewalk.tools.tool_runner import get_tool_commands, run_command


@dataclass
class StaticIssue:
    """Normalized static-analysis finding."""

    file_path: str
    line: int | None
    severity: str  # critical, warning, info
    rule: str
    message: str
    category: str  # bug, security, style, type_error, etc.
    tool: str
    column: int | None = None


SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


def _detect_language(file_path: str) -> str:
    """Map file extension to a language identifier."""
    ext = Path(file_path).suffix.lower()
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
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".php": "php",
        ".rb": "ruby",
        ".cs": "csharp",
        ".dart": "dart",
    }
    return mapping.get(ext, "")


def _default_analyzer_commands(language: str, file_paths: list[str]) -> list[tuple[str, list[str]]]:
    """Return (tool_name, command) pairs for a language."""
    joined = " ".join(file_paths)
    if language == "python":
        return [
            ("ruff", ["ruff", "check", "--output-format", "json"] + file_paths),
            ("mypy", ["mypy", "--no-error-summary"] + file_paths),
            ("bandit", ["bandit", "-f", "json", "-q", "-r"] + file_paths),
        ]
    if language in ("js", "ts"):
        return [("eslint", ["eslint", "--format", "json"] + file_paths)]
    if language == "go":
        return [("go vet", ["go", "vet"] + file_paths)]
    if language == "rust":
        return [("cargo check", ["cargo", "check", "--message-format=json"])]
    if language == "java":
        return [("mvn compile", ["mvn", "compile", "-q"])]
    return []


def _parse_ruff_json(raw: str) -> list[StaticIssue]:
    """Parse Ruff JSON output."""
    issues = []
    try:
        data = json.loads(raw)
        for item in data:
            issues.append(StaticIssue(
                file_path=item.get("filename", ""),
                line=item.get("location", {}).get("row") or None,
                column=item.get("location", {}).get("column") or None,
                severity="warning" if item.get("code", "").startswith("E") else "info",
                rule=item.get("code", ""),
                message=item.get("message", ""),
                category="style" if item.get("code", "").startswith(("E", "W", "F")) else "bug",
                tool="ruff",
            ))
    except json.JSONDecodeError:
        pass
    return issues


def _parse_mypy_plain(raw: str) -> list[StaticIssue]:
    """Parse mypy plain-text output."""
    issues = []
    for line in raw.splitlines():
        # Format: file.py:12: error: Message  [error-code]
        match = re.match(r"^(.+?):(\d+):(\d+)?\s*(error|warning|note):\s*(.+)$", line)
        if match:
            file_path, line_no, col, level, message = match.groups()
            issues.append(StaticIssue(
                file_path=file_path,
                line=int(line_no) if line_no else None,
                column=int(col) if col else None,
                severity="warning" if level == "error" else "info",
                rule="mypy",
                message=message.strip(),
                category="type_error",
                tool="mypy",
            ))
    return issues


def _parse_bandit_json(raw: str) -> list[StaticIssue]:
    """Parse Bandit JSON output."""
    issues = []
    try:
        data = json.loads(raw)
        for item in data.get("results", []):
            issues.append(StaticIssue(
                file_path=item.get("filename", ""),
                line=item.get("line_number"),
                severity=(item.get("issue_severity", "")).lower(),
                rule=item.get("test_id", ""),
                message=item.get("issue_text", ""),
                category="security",
                tool="bandit",
            ))
    except json.JSONDecodeError:
        pass
    return issues


def _parse_eslint_json(raw: str) -> list[StaticIssue]:
    """Parse ESLint JSON output."""
    issues = []
    try:
        data = json.loads(raw)
        for file_item in data:
            file_path = file_item.get("filePath", "")
            for msg in file_item.get("messages", []):
                severity = {1: "info", 2: "warning"}.get(msg.get("severity"), "warning")
                issues.append(StaticIssue(
                    file_path=file_path,
                    line=msg.get("line"),
                    column=msg.get("column"),
                    severity=severity,
                    rule=msg.get("ruleId", ""),
                    message=msg.get("message", ""),
                    category="style" if severity == "info" else "bug",
                    tool="eslint",
                ))
    except json.JSONDecodeError:
        pass
    return issues


def _parse_cargo_json(raw: str) -> list[StaticIssue]:
    """Parse cargo check JSON output (compiler messages)."""
    issues = []
    for line in raw.splitlines():
        try:
            item = json.loads(line)
            if item.get("reason") != "compiler-message":
                continue
            msg = item.get("message", {})
            spans = msg.get("spans", [])
            for span in spans:
                issues.append(StaticIssue(
                    file_path=span.get("file_name", ""),
                    line=span.get("line_start"),
                    column=span.get("column_start"),
                    severity="warning" if msg.get("level") == "error" else "info",
                    rule=msg.get("code", {}).get("code", "") if isinstance(msg.get("code"), dict) else "",
                    message=msg.get("message", ""),
                    category="type_error" if "type" in msg.get("message", "").lower() else "bug",
                    tool="cargo",
                ))
        except json.JSONDecodeError:
            continue
    return issues


def _parse_generic(raw: str, tool: str) -> list[StaticIssue]:
    """Fallback parser: create one issue from non-empty stderr/stdout."""
    text = raw.strip()
    if not text:
        return []
    return [StaticIssue(
        file_path="",
        line=None,
        severity="info",
        rule=tool,
        message=text[:500],
        category="hygiene",
        tool=tool,
    )]


def _parse_output(tool: str, raw_stdout: str, raw_stderr: str) -> list[StaticIssue]:
    """Dispatch to the right parser based on tool name."""
    raw = raw_stdout + ("\n" + raw_stderr if raw_stderr else "")
    parsers = {
        "ruff": _parse_ruff_json,
        "mypy": _parse_mypy_plain,
        "bandit": _parse_bandit_json,
        "eslint": _parse_eslint_json,
        "cargo check": _parse_cargo_json,
    }
    parser = parsers.get(tool, _parse_generic)
    try:
        if parser is _parse_generic:
            return parser(raw, tool)
        return parser(raw)
    except Exception as e:
        _log(f"[static_analysis] parser error for {tool}: {e}")
        return []


def run_static_analysis(
    repo_path: str,
    file_paths: list[str],
    language_hint: str | None = None,
) -> list[StaticIssue]:
    """Run static analyzers appropriate for the given files.

    Args:
        repo_path: Root of the repository.
        file_paths: Relative paths to files to analyze.
        language_hint: Optional language override.

    Returns:
        Normalized list of StaticIssue findings.
    """
    if not file_paths:
        return []

    language = language_hint or _detect_language(file_paths[0])
    if not language:
        _log(f"[static_analysis] unknown language for {file_paths[0]}")
        return []

    # Check for configured commands first
    configured = get_tool_commands(repo_path, "static_analysis", language)
    if configured:
        tool_commands = [(f"configured-{i}", cmd) for i, cmd in enumerate(configured)]
    else:
        tool_commands = _default_analyzer_commands(language, file_paths)

    all_issues: list[StaticIssue] = []
    for tool, cmd in tool_commands:
        result = run_command(cmd, cwd=repo_path)
        if result.get("error") == f"Command not found: {cmd[0]}":
            _log(f"[static_analysis] {tool} not installed; skipping")
            continue
        if result.get("error"):
            _log(f"[static_analysis] {tool} error: {result['error']}")
            continue
        issues = _parse_output(tool, result.get("stdout", ""), result.get("stderr", ""))
        # Filter issues to requested files
        target_files = set(file_paths)
        for issue in issues:
            if not target_files or issue.file_path in target_files or not issue.file_path:
                all_issues.append(issue)

    # Sort by severity then file
    all_issues.sort(key=lambda i: (SEVERITY_RANK.get(i.severity, 1), i.file_path or "", i.line or 0))
    _log(f"[static_analysis] {language}: {len(all_issues)} issues from {len(file_paths)} files")
    return all_issues
