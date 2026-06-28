"""Apply code fixes with robust matching, formatting, and validation.

Supports:
- exact text replacement
- context-line disambiguation for non-unique snippets
- unified-diff patch fallback
- optional formatter execution
- optional post-apply validation (AST parse for Python)
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
from pathlib import Path


def _resolve_target_path(repo_path: str, file_path: str) -> Path:
    """Resolve target path and enforce it stays inside the repo."""
    full_path = Path(repo_path) / file_path if not os.path.isabs(file_path) else Path(file_path)
    try:
        full_path.resolve().relative_to(Path(repo_path).resolve())
    except ValueError:
        raise ValueError(f"Path traversal blocked: {file_path}")
    return full_path


def _find_unique_match(content: str, old_code: str, context_lines: int = 3) -> tuple[int, str] | None:
    """Find a unique occurrence of old_code, optionally using surrounding context.

    Returns (start_index, matched_text) or None if not found/ambiguous.
    """
    # 1. Exact match
    count = content.count(old_code)
    if count == 1:
        return content.find(old_code), old_code
    if count == 0:
        # 2. Try whitespace-normalized match
        normalized_content = re.sub(r"[ \t]+", " ", content)
        normalized_old = re.sub(r"[ \t]+", " ", old_code)
        if normalized_content.count(normalized_old) == 1:
            # Map back to original content is tricky; just use normalized replace.
            return -1, normalized_old  # sentinel: use normalized replacement

    if count > 1:
        # 3. Disambiguate with surrounding context lines
        content_lines = content.splitlines()
        old_lines = old_code.strip().splitlines()
        matches = []
        for i in range(len(content_lines) - len(old_lines) + 1):
            block = content_lines[i : i + len(old_lines)]
            if "\n".join(block) == "\n".join(old_lines):
                # Build context window
                start_ctx = max(0, i - context_lines)
                end_ctx = min(len(content_lines), i + len(old_lines) + context_lines)
                context_block = content_lines[start_ctx:end_ctx]
                matches.append((i, "\n".join(context_block)))

        if len(matches) == 1:
            idx, matched = matches[0]
            return len("\n".join(content_lines[:idx]) + ("\n" if idx > 0 else "")), matched

    return None


def _flexible_whitespace_pattern(text: str) -> str:
    """Build a regex that matches ``text`` while allowing horizontal whitespace
    runs and line-ending variations to differ.
    """
    parts = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] in " \t":
            while i < n and text[i] in " \t":
                i += 1
            parts.append(r"[ \t]+")
        elif text[i] in "\r\n":
            while i < n and text[i] in "\r\n":
                i += 1
            parts.append(r"\r?\n")
        else:
            j = i
            while j < n and text[j] not in " \t\r\n":
                j += 1
            parts.append(re.escape(text[i:j]))
            i = j
    return "".join(parts)


def _apply_normalized(content: str, old_code: str, new_code: str) -> str:
    """Apply replacement after normalizing horizontal whitespace.

    Finds the unique occurrence of ``old_code`` in ``content`` while tolerating
    horizontal whitespace differences, then replaces the matched original span.
    """
    normalized_content = re.sub(r"[ \t]+", " ", content)
    normalized_old = re.sub(r"[ \t]+", " ", old_code)
    if normalized_content.count(normalized_old) != 1:
        return content

    pattern = _flexible_whitespace_pattern(old_code)
    match = re.search(pattern, content)
    if not match:
        return content
    return content[: match.start()] + new_code + content[match.end() :]


def _apply_patch(file_path: Path, patch_text: str) -> bool:
    """Try to apply a unified diff patch to the file.

    Returns True if the patch applied cleanly, False otherwise.
    """
    try:
        # Apply patch in the parent directory so relative paths work.
        result = subprocess.run(
            ["patch", "-p0", "-i", "-"],
            input=patch_text,
            cwd=str(file_path.parent),
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        # patch command not available
        return False
    except Exception:
        return False


def _detect_formatter(repo_path: str, file_path: str) -> list[str] | None:
    """Detect a suitable formatter command for the file type.

    Checks codewalk.yaml, pyproject.toml, package.json, then falls back to common tools.
    """
    repo = Path(repo_path)
    ext = Path(file_path).suffix

    # Look for explicit formatter config in codewalk.yaml
    try:
        from src.codewalk.codewalk_config import load_codewalk_yaml
        config = load_codewalk_yaml(repo_path)
        fmt = config.get("tools", {}).get("formatter")
        if fmt:
            if isinstance(fmt, str):
                return fmt.split()
            return fmt
    except Exception:
        pass

    if ext == ".py":
        if (repo / "pyproject.toml").exists():
            return ["ruff", "format", file_path]
        if shutil.which("ruff"):
            return ["ruff", "format", file_path]
        if shutil.which("black"):
            return ["black", file_path]
    elif ext in (".js", ".jsx", ".ts", ".tsx"):
        if shutil.which("prettier"):
            return ["prettier", "--write", file_path]
    elif ext in (".go",):
        if shutil.which("gofmt"):
            return ["gofmt", "-w", file_path]

    return None


def _run_formatter(repo_path: str, file_path: str) -> dict | None:
    """Run detected formatter on the file. Returns None if no formatter found."""
    cmd = _detect_formatter(repo_path, file_path)
    if not cmd:
        return None
    try:
        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        return {"ok": result.returncode == 0, "stdout": result.stdout, "stderr": result.stderr}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}


def _validate_file(file_path: Path) -> dict | None:
    """Validate that the modified file is still syntactically valid.

    Currently supports Python via ast.parse. Other languages are best-effort
    (no validation) unless a parser is added later.
    """
    if file_path.suffix != ".py":
        return None
    try:
        source = file_path.read_text(errors="replace")
        ast.parse(source)
        return {"ok": True, "message": "Python syntax valid"}
    except SyntaxError as e:
        return {"ok": False, "message": f"Python syntax error after fix: {e}"}
    except Exception as e:
        return {"ok": False, "message": f"Validation error: {e}"}


def _load_codewalk_yaml_tools(repo_path: str) -> dict:
    """Load tool overrides from codewalk.yaml if present."""
    try:
        from src.codewalk.codewalk_config import load_codewalk_yaml
        config = load_codewalk_yaml(repo_path)
        return config.get("tools", {})
    except Exception:
        return {}


def apply_fix_to_file(
    repo_path: str,
    file_path: str,
    old_code: str,
    new_code: str,
    *,
    context_lines: int = 3,
    validate_only: bool = False,
    run_formatter: bool = True,
    allow_patch: bool = True,
) -> dict:
    """Apply a single fix by robust text replacement.

    Safety rules:
      - target file must be inside repo_path
      - old_code must match uniquely (exact, normalized, or with context lines)
      - write is atomic (temp file + rename)
      - optionally validates Python syntax after applying

    Returns {"ok": True, ...} or {"ok": False, "error": ...}
    """
    try:
        full_path = _resolve_target_path(repo_path, file_path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    if not full_path.exists():
        return {"ok": False, "error": f"File not found: {file_path}"}

    try:
        content = full_path.read_text(errors="replace")
    except OSError as e:
        return {"ok": False, "error": f"Cannot read file: {e}"}

    # Try patch fallback first if old_code looks like a unified diff
    if allow_patch and old_code.strip().startswith("--- ") and "+++ " in old_code:
        if validate_only:
            return {"ok": True, "message": "Patch would be applied (validate_only)", "file_path": file_path}
        if _apply_patch(full_path, old_code):
            return {"ok": True, "message": f"Patch applied to {file_path}", "file_path": file_path}
        return {"ok": False, "error": f"Could not apply patch to {file_path}"}

    match = _find_unique_match(content, old_code, context_lines=context_lines)
    if match is None:
        exact_count = content.count(old_code)
        if exact_count > 1:
            return {
                "ok": False,
                "error": (
                    f"Ambiguous replacement: the old_code appears {exact_count} times in {file_path}.\n\n"
                    f"Please provide a more specific old_code that includes surrounding context "
                    f"(2-3 extra lines above and below) to make the match unique."
                ),
            }
        return {
            "ok": False,
            "error": (
                f"Could not find the specified code in {file_path}.\n\n"
                f"The old_code does not match the file content exactly.\n"
                f"Try including 2-3 extra lines of surrounding context, or pass a unified diff as old_code."
            ),
        }

    start_idx, matched_text = match
    if start_idx == -1:
        # Normalized whitespace match
        new_content = _apply_normalized(content, old_code, new_code)
    else:
        # Replace only old_code, not the surrounding context block that may
        # have been returned for disambiguation.
        new_content = content[:start_idx] + new_code + content[start_idx + len(old_code) :]

    if validate_only:
        return {
            "ok": True,
            "message": f"Fix would be applied to {file_path} (validate_only)",
            "file_path": file_path,
        }

    # Atomic write with rollback backup
    temp_path = full_path.with_suffix(full_path.suffix + ".tmp")
    backup_path = full_path.with_suffix(full_path.suffix + ".bak")
    try:
        temp_path.write_text(new_content, errors="replace")
        # Keep a backup of the original content before replacing.
        backup_path.write_text(content, errors="replace")
        temp_path.replace(full_path)
    except OSError as e:
        # Clean up temp/backup files on write failure.
        for p in (temp_path, backup_path):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        return {"ok": False, "error": f"Cannot write file: {e}"}

    # Validation
    validation = _validate_file(full_path)
    if validation and not validation["ok"]:
        # Roll back to the original content.
        try:
            backup_path.replace(full_path)
        except OSError as rollback_err:
            return {
                "ok": False,
                "error": (
                    f"Validation failed: {validation['message']}. "
                    f"Rollback also failed: {rollback_err}. "
                    f"Original content is in {backup_path}"
                ),
            }
        return {"ok": False, "error": validation["message"]}

    # Success: remove backup.
    try:
        backup_path.unlink(missing_ok=True)
    except OSError:
        pass

    # Formatting
    formatter_result = None
    if run_formatter:
        formatter_result = _run_formatter(repo_path, file_path)

    return {
        "ok": True,
        "message": f"Fix applied to {file_path}",
        "file_path": file_path,
        "old_code": old_code,
        "new_code": new_code,
        "validation": validation,
        "formatter": formatter_result,
    }


def apply_fixes_batch(
    repo_path: str,
    fixes: list[dict],
    *,
    context_lines: int = 3,
    validate_only: bool = False,
    run_formatter: bool = True,
    continue_on_error: bool = False,
) -> dict:
    """Apply multiple fixes sequentially.

    Args:
        repo_path: Root of the repository
        fixes:     List of {"file_path": str, "old_code": str, "new_code": str}
        continue_on_error: If True, keep applying remaining fixes after one fails.

    Returns:
        {
          "applied": [list of successful fixes],
          "failed":  [list of failed fix dicts with index and error] or null,
          "total":   N
        }
    """
    applied = []
    failed = []

    for index, fix in enumerate(fixes):
        result = apply_fix_to_file(
            repo_path,
            fix["file_path"],
            fix["old_code"],
            fix["new_code"],
            context_lines=context_lines,
            validate_only=validate_only,
            run_formatter=run_formatter,
        )
        if result["ok"]:
            applied.append(result)
        else:
            failed.append({"index": index, **result})
            if not continue_on_error:
                break

    return {
        "applied": applied,
        "failed": failed if failed else None,
        "total": len(fixes),
    }
