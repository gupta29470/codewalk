"""Unified code editor.

Supports two modes:
1. Exact text replacement (fast path, used by agent/chat apply_fix tools).
2. LLM-as-editor (used by review preview-edits): read current file, prompt LLM
   for SEARCH/REPLACE blocks, apply, validate syntax (language-aware via
   ast/tree-sitter), retry on failure — including empty responses.

apply_edit(dry_run=True) returns original/modified content without writing —
the API preview flow. write_approved_edit() writes user-approved diffs;
verify_and_rollback() runs static analysis + tests and rolls back failures.
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.codewalk.log import log as _log

@dataclass
class SearchReplaceBlock:
    """One SEARCH/REPLACE edit block parsed from an LLM response."""

    search: str
    replace: str


_BLOCK_RE = re.compile(
    r"<<<<<<<\s*SEARCH\n(.*?)\n=======\n(.*?)\n>>>>>>>\s*REPLACE",
    re.DOTALL,
)


def parse_search_replace_blocks(text: str) -> list[SearchReplaceBlock]:
    """Extract SEARCH/REPLACE blocks from an LLM response."""
    text = text or ""
    blocks: list[SearchReplaceBlock] = []
    for match in _BLOCK_RE.finditer(text):
        blocks.append(SearchReplaceBlock(search=match.group(1), replace=match.group(2)))
    if not blocks and ("<<<<<<<" in text or "SEARCH" in text or "REPLACE" in text):
        raise ValueError("Found SEARCH/REPLACE markers but could not parse a valid block")
    return blocks


def apply_search_replace_blocks(file_content: str, blocks: list[SearchReplaceBlock]) -> str:
    """Apply search/replace blocks sequentially.

    Each search string must match exactly once in the current file content.
    """
    for idx, block in enumerate(blocks, start=1):
        search = block.search
        replace = block.replace
        count = file_content.count(search)
        if count == 0:
            raise ValueError(f"Block #{idx}: search text not found")
        if count > 1:
            raise ValueError(f"Block #{idx}: search text matches {count} times; expected exactly one")
        file_content = file_content.replace(search, replace, 1)
    return file_content


def _resolve_target_path(repo_path: str, file_path: str) -> Path:
    """Resolve target path and enforce it stays inside the repo."""
    full_path = Path(repo_path) / file_path if not os.path.isabs(file_path) else Path(file_path)
    try:
        full_path.resolve().relative_to(Path(repo_path).resolve())
    except ValueError:
        raise ValueError(f"Path traversal blocked: {file_path}")
    return full_path


def _find_unique_match(content: str, old_code: str, context_lines: int = 3) -> tuple[int, str] | None:
    """Find a unique occurrence of old_code, optionally using surrounding context."""
    count = content.count(old_code)
    if count == 1:
        return content.find(old_code), old_code
    if count == 0:
        normalized_content = re.sub(r"[ \t]+", " ", content)
        normalized_old = re.sub(r"[ \t]+", " ", old_code)
        if normalized_content.count(normalized_old) == 1:
            return -1, normalized_old  # sentinel: use normalized replacement

    if count > 1:
        content_lines = content.splitlines()
        old_lines = old_code.strip().splitlines()
        matches = []
        for i in range(len(content_lines) - len(old_lines) + 1):
            block = content_lines[i : i + len(old_lines)]
            if "\n".join(block) == "\n".join(old_lines):
                start_ctx = max(0, i - context_lines)
                end_ctx = min(len(content_lines), i + len(old_lines) + context_lines)
                context_block = content_lines[start_ctx:end_ctx]
                matches.append((i, "\n".join(context_block)))

        if len(matches) == 1:
            idx, matched = matches[0]
            return len("\n".join(content_lines[:idx]) + ("\n" if idx > 0 else "")), matched

    return None


def _flexible_whitespace_pattern(text: str) -> str:
    """Build a regex that matches ``text`` tolerating whitespace differences."""
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
    """Apply replacement after normalizing horizontal whitespace."""
    normalized_content = re.sub(r"[ \t]+", " ", content)
    normalized_old = re.sub(r"[ \t]+", " ", old_code)
    if normalized_content.count(normalized_old) != 1:
        return content

    pattern = _flexible_whitespace_pattern(old_code)
    match = re.search(pattern, content)
    if not match:
        return content
    return content[: match.start()] + new_code + content[match.end() :]


def _apply_exact(
    content: str,
    old_code: str,
    new_code: str,
    *,
    context_lines: int = 3,
) -> tuple[str | None, str | None]:
    """Try exact/old-code replacement. Returns (new_content, error)."""
    match = _find_unique_match(content, old_code, context_lines=context_lines)
    if match is None:
        exact_count = content.count(old_code)
        if exact_count > 1:
            return None, (
                f"Ambiguous replacement: the old_code appears {exact_count} times. "
                "Provide more surrounding context to make the match unique."
            )
        return None, "Could not find the specified code in the file."

    start_idx, matched_text = match
    if start_idx == -1:
        new_content = _apply_normalized(content, old_code, new_code)
    else:
        new_content = content[:start_idx] + new_code + content[start_idx + len(old_code) :]
    return new_content, None


_EXT_TO_TS_LANGUAGE = {
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript",  # tree-sitter-javascript handles JSX natively
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".tsx": "tsx",         # needs the TSX grammar, not plain TypeScript
    ".dart": "dart", ".java": "java", ".go": "go", ".rs": "rust",
    ".rb": "ruby", ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
    ".cs": "csharp", ".php": "php", ".kt": "kotlin", ".swift": "swift",
}

_ts_parser_cache: dict[str, Any] = {}


def _get_ts_parser(lang_key: str):
    """Return a cached tree-sitter Parser for the language key, or None."""
    if lang_key in _ts_parser_cache:
        return _ts_parser_cache[lang_key]

    parser = None
    try:
        from tree_sitter import Language, Parser
        if lang_key == "tsx":
            import tree_sitter_typescript as _tst
            lang = Language(_tst.language_tsx())
        else:
            from src.codewalk.analysis.code_parser import get_language
            lang = get_language(lang_key)
        if lang is not None:
            parser = Parser(lang)
    except Exception:
        parser = None

    _ts_parser_cache[lang_key] = parser
    return parser


def _first_error_node(node):
    """Depth-first search for the first ERROR/missing node in a parse tree."""
    if node.type == "ERROR" or node.is_missing:
        return node
    for child in node.children:
        if child.has_error or child.is_missing:
            found = _first_error_node(child)
            if found is not None:
                return found
    return None


def _validate_syntax_source(source: str, ext: str) -> tuple[bool, str]:
    """Validate source text in memory. Same rules as _validate_syntax."""
    if ext == ".py":
        try:
            ast.parse(source)
            return True, "Python syntax valid"
        except SyntaxError as e:
            return False, f"Python syntax error after fix: {e}"
        except Exception as e:
            return False, f"Validation error: {e}"

    lang_key = _EXT_TO_TS_LANGUAGE.get(ext)
    if lang_key is None:
        return True, "No syntax validator for this file type"

    parser = _get_ts_parser(lang_key)
    if parser is None:
        return True, "No syntax validator available for this file type"

    try:
        tree = parser.parse(source.encode("utf-8"))
    except Exception:
        return True, "Syntax validation skipped (parser error)"

    if tree.root_node.has_error:
        node = _first_error_node(tree.root_node)
        if node is not None:
            row, _ = node.start_point
            return False, f"Syntax error after fix ({ext}) near line {row + 1}: {node.type}"
        return False, f"Syntax error after fix ({ext})"
    return True, "Syntax valid"


def _validate_syntax(file_path: Path) -> tuple[bool, str]:
    """Language-aware syntax check after an edit.

    .py → ast.parse; other supported languages → tree-sitter parse with a
    has_error check; unknown extensions → validation skipped (ok=True).
    A missing/broken grammar never blocks an edit — validation is skipped.
    """
    ext = file_path.suffix.lower()
    try:
        source = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return False, f"Validation error: {e}"
    return _validate_syntax_source(source, ext)


def _detect_formatter(repo_path: str, file_path: str) -> list[str] | None:
    """Detect a suitable formatter command for the file type."""
    repo = Path(repo_path)
    ext = Path(file_path).suffix

    try:
        from src.codewalk.codewalk_config import load_codewalk_yaml
        config = load_codewalk_yaml(repo_path)
        fmt = config.get("tools", {}).get("formatter")
        if fmt:
            return fmt.split() if isinstance(fmt, str) else fmt
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
    """Run detected formatter on the file."""
    cmd = _detect_formatter(repo_path, file_path)
    if not cmd:
        return None
    try:
        result = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True)
        return {"ok": result.returncode == 0, "stdout": result.stdout, "stderr": result.stderr}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}


_FENCE_LANG = {
    ".py": "python", ".js": "javascript", ".jsx": "jsx", ".mjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx", ".go": "go", ".rs": "rust",
    ".java": "java", ".rb": "ruby", ".dart": "dart", ".c": "c",
    ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp",
    ".php": "php", ".kt": "kotlin", ".swift": "swift",
}


def _fence_lang(file_path: str) -> str:
    """Return the code-fence language tag for a file path ("" if unknown)."""
    return _FENCE_LANG.get(Path(file_path).suffix.lower(), "")


def _build_edit_prompt(file_path: str, file_content: str, finding: dict[str, Any]) -> str:
    """Build a prompt that asks the LLM to emit SEARCH/REPLACE blocks."""
    title = finding.get("title", "")
    explanation = finding.get("explanation", "")
    current_code = finding.get("current_code", "") or ""
    recommended_code = finding.get("recommended_code", "") or ""
    line_number = finding.get("line_number")

    location = f"{file_path}"
    if line_number:
        location = f"{file_path}:{line_number}"

    lang = _fence_lang(file_path)

    current_hint = ""
    if current_code.strip():
        current_hint = f"\nThe reviewer identified this existing code:\n```{lang}\n{current_code}\n```\n"

    recommended_hint = ""
    if recommended_code.strip():
        recommended_hint = f"\nThe reviewer suggested this replacement:\n```{lang}\n{recommended_code}\n```\n"

    return f"""You are a precise code editor.

Apply the change described below to the file by emitting one or more SEARCH/REPLACE blocks.

## File: {location}

```{lang}
{file_content}
```

## Change to apply

**Title:** {title}

**Explanation:** {explanation}
{current_hint}{recommended_hint}

## Rules

1. Emit edits in this exact format. SEARCH must match the file content exactly once.

```text
<<<<<<< SEARCH
[exact existing code]
=======
[exact new code]
>>>>>>> REPLACE
```

2. Do not explain. Output only SEARCH/REPLACE blocks.
3. Preserve indentation exactly.
4. If the suggested replacement is wrong for the current file, fix it yourself.
5. If you cannot make the change safely, output nothing and the operation will be skipped.

## Edits
"""


def _apply_llm_edit(
    llm,
    repo_path: str,
    file_path: str,
    finding: dict[str, Any],
    original_content: str,
    *,
    max_attempts: int = 3,
    error_feedback: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """LLM-as-editor loop. Returns result dict.

    ``error_feedback`` seeds external failure context (e.g. static-analysis
    or test output from a previous verify round) into the first prompt.
    ``dry_run`` returns the proposed content without writing to disk.
    """
    full_path = _resolve_target_path(repo_path, file_path)
    content = original_content
    last_error = error_feedback or ""

    for attempt in range(1, max_attempts + 1):
        prompt = _build_edit_prompt(file_path, content, finding)
        if last_error:
            prompt += f"\n\nPrevious attempt failed: {last_error}\nCorrect the edit above."

        try:
            response = llm.invoke(
                [("human", prompt)],
            )
            response_text = response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            _log(f"[editor] LLM call failed on attempt {attempt}: {e}")
            last_error = f"LLM call failed: {e}"
            continue

        try:
            blocks = parse_search_replace_blocks(response_text)
        except ValueError as e:
            _log(f"[editor] Could not parse blocks on attempt {attempt}: {e}")
            last_error = str(e)
            continue

        if not blocks:
            _log(f"[editor] LLM returned no blocks on attempt {attempt}")
            last_error = (
                "You returned no SEARCH/REPLACE blocks. Output at least one block "
                "in the exact SEARCH/REPLACE format."
            )
            continue

        try:
            new_content = apply_search_replace_blocks(content, blocks)
        except ValueError as e:
            _log(f"[editor] Could not apply blocks on attempt {attempt}: {e}")
            last_error = str(e)
            continue

        if dry_run:
            ok, message = _validate_syntax_source(new_content, full_path.suffix.lower())
            if ok:
                return {
                    "ok": True,
                    "file_path": file_path,
                    "original_content": original_content,
                    "modified_content": new_content,
                    "validation": {"ok": True, "message": message},
                    "attempts": attempt,
                }
            last_error = message
            content = original_content
            continue

        # Atomic write with backup.
        temp_path = full_path.with_suffix(full_path.suffix + ".cwtmp")
        backup_path = full_path.with_suffix(full_path.suffix + ".cwbak")
        try:
            temp_path.write_text(new_content, encoding="utf-8")
            backup_path.write_text(original_content, encoding="utf-8")
            temp_path.replace(full_path)
        except OSError as e:
            return {"ok": False, "file_path": file_path, "error": f"Cannot write file: {e}"}

        ok, message = _validate_syntax(full_path)
        if ok:
            try:
                backup_path.unlink(missing_ok=True)
            except OSError:
                pass
            return {
                "ok": True,
                "file_path": file_path,
                "message": f"Fix applied to {file_path}",
                "attempts": attempt,
            }

        last_error = message
        try:
            backup_path.replace(full_path)
        except OSError as rollback_err:
            return {
                "ok": False,
                "file_path": file_path,
                "error": f"{message}. Rollback failed: {rollback_err}",
                "attempts": attempt,
            }
        content = original_content

    return {
        "ok": False,
        "file_path": file_path,
        "error": f"Failed after {max_attempts} attempts. Last error: {last_error}",
        "attempts": max_attempts,
    }


def apply_edit(
    repo_path: str,
    file_path: str,
    *,
    old_code: str | None = None,
    new_code: str | None = None,
    finding: dict[str, Any] | None = None,
    llm=None,
    context_lines: int = 3,
    run_formatter: bool = True,
    max_attempts: int = 3,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Unified code editor.

    Two usage patterns:

    1. Exact replacement (fast, no LLM needed):
       apply_edit(repo_path, file_path, old_code="x = 1", new_code="x = 42")

    2. LLM-as-editor from a finding:
       apply_edit(repo_path, file_path, finding={...}, llm=llm)

    When both old_code/new_code and a finding/llm are provided, the fast exact
    path is tried first; if it fails, the LLM-as-editor fallback is used.

    With dry_run=True, nothing is written — the result contains
    original_content/modified_content for preview (diff review before apply).

    Returns {"ok": True, "file_path": ..., "message": ...} or
            {"ok": False, "file_path": ..., "error": ...}.
    """
    try:
        full_path = _resolve_target_path(repo_path, file_path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    if not full_path.exists():
        return {"ok": False, "error": f"File not found: {file_path}"}

    try:
        original_content = full_path.read_text(errors="replace")
    except OSError as e:
        return {"ok": False, "error": f"Cannot read file: {e}"}

    # Try fast exact replacement first if old_code/new_code are given.
    if old_code is not None and new_code is not None:
        new_content, error = _apply_exact(original_content, old_code, new_code, context_lines=context_lines)
        if new_content is not None:
            if dry_run:
                ok, message = _validate_syntax_source(new_content, full_path.suffix.lower())
                if not ok:
                    return {"ok": False, "error": message}
                return {
                    "ok": True,
                    "file_path": file_path,
                    "original_content": original_content,
                    "modified_content": new_content,
                    "validation": {"ok": True, "message": message},
                }
            try:
                temp_path = full_path.with_suffix(full_path.suffix + ".cwtmp")
                backup_path = full_path.with_suffix(full_path.suffix + ".cwbak")
                temp_path.write_text(new_content, errors="replace")
                backup_path.write_text(original_content, errors="replace")
                temp_path.replace(full_path)
            except OSError as e:
                return {"ok": False, "error": f"Cannot write file: {e}"}

            validation = _validate_syntax(full_path)
            if not validation[0]:
                try:
                    backup_path.replace(full_path)
                except OSError as rollback_err:
                    return {
                        "ok": False,
                        "error": f"{validation[1]}. Rollback failed: {rollback_err}",
                    }
                return {"ok": False, "error": validation[1]}

            try:
                backup_path.unlink(missing_ok=True)
            except OSError:
                pass

            formatter_result = _run_formatter(repo_path, file_path) if run_formatter else None
            return {
                "ok": True,
                "file_path": file_path,
                "message": f"Fix applied to {file_path}",
                "validation": {"ok": True, "message": validation[1]},
                "formatter": formatter_result,
            }

        # Exact replacement failed. Fall back to LLM editor if available.
        if finding is not None and llm is not None:
            _log("[editor] Exact replacement failed; falling back to LLM editor.")
        else:
            return {"ok": False, "error": error}

    # LLM-as-editor path.
    if finding is None:
        return {"ok": False, "error": "No finding provided for LLM-as-editor mode"}
    if llm is None:
        return {"ok": False, "error": "LLM is required for finding-based editing"}

    result = _apply_llm_edit(llm, repo_path, file_path, finding, original_content, max_attempts=max_attempts, dry_run=dry_run)
    if result["ok"] and run_formatter and not dry_run:
        formatter_result = _run_formatter(repo_path, file_path)
        result["formatter"] = formatter_result
    return result


def write_approved_edit(
    repo_path: str,
    file_path: str,
    modified_content: str,
    *,
    expected_original: str | None = None,
    run_formatter: bool = True,
) -> dict[str, Any]:
    """Write a previously-previewed (user-approved) edit to disk.

    Atomic write with backup, language-aware syntax validation, rollback on
    failure, optional formatter. The content was already reviewed by the user,
    so no LLM is involved here.

    ``expected_original`` is the content the user previewed. When provided and
    the file on disk no longer matches it, the write is refused — the file
    changed since the preview, and writing would silently clobber the newer
    changes.

    Returns {"ok": True, ..., "original_content": ...} (kept for verify
    rollback) or {"ok": False, "error": ...}.
    """
    try:
        full_path = _resolve_target_path(repo_path, file_path)
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    if not full_path.exists():
        return {"ok": False, "error": f"File not found: {file_path}"}

    try:
        original_content = full_path.read_text(errors="replace")
    except OSError as e:
        return {"ok": False, "error": f"Cannot read file: {e}"}

    if expected_original is not None and original_content != expected_original:
        return {
            "ok": False,
            "error": "File changed since preview — re-preview before applying",
        }

    temp_path = full_path.with_suffix(full_path.suffix + ".cwtmp")
    backup_path = full_path.with_suffix(full_path.suffix + ".cwbak")
    try:
        temp_path.write_text(modified_content, errors="replace")
        backup_path.write_text(original_content, errors="replace")
        temp_path.replace(full_path)
    except OSError as e:
        return {"ok": False, "error": f"Cannot write file: {e}"}

    ok, message = _validate_syntax(full_path)
    if not ok:
        try:
            backup_path.replace(full_path)
        except OSError as rollback_err:
            return {"ok": False, "error": f"{message}. Rollback failed: {rollback_err}"}
        return {"ok": False, "error": message}

    try:
        backup_path.unlink(missing_ok=True)
    except OSError:
        pass

    formatter_result = _run_formatter(repo_path, file_path) if run_formatter else None
    return {
        "ok": True,
        "file_path": file_path,
        "message": f"Edit written to {file_path}",
        "original_content": original_content,
        "validation": {"ok": True, "message": message},
        "formatter": formatter_result,
    }


def _sa_error_issues(issues: list) -> list:
    """Filter static-analysis issues to error-level severities."""
    return [
        i for i in issues
        if getattr(i, "severity", "").lower() in ("critical", "high", "warning")
    ]


def sa_has_errors(issues: list) -> bool:
    """True if any static-analysis issue is above informational severity."""
    return bool(_sa_error_issues(issues))


def verify_and_rollback(
    repo_path: str,
    modified_files: list[str],
    originals: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Run static analysis + tests on modified files; roll back on failure.

    This never re-edits: the user approved exact diffs, so a failed
    verification rolls the offending files back to their original content
    instead of changing what was approved.

    Args:
        repo_path: Repo root.
        modified_files: Files that were just written.
        originals: {file_path: content before edit}. Files missing from the
            map are treated as pre-existing and are not rolled back.

    Returns {
        "sa_issues": [...],
        "test_result": ExecutionResult | None,
        "verification_passed": bool,
        "rolled_back_files": [...],
    }
    """
    from src.codewalk.tools.static_analysis import run_static_analysis
    from src.codewalk.tools.test_runner import run_tests

    originals = originals or {}
    sa_issues: list = run_static_analysis(repo_path, modified_files) if modified_files else []
    test_result = run_tests(repo_path, modified_files) if modified_files else None

    sa_errors = _sa_error_issues(sa_issues)
    tests_ok = test_result is None or test_result.ok
    rolled_back: list[str] = []

    if modified_files and (sa_errors or not tests_ok):
        offending = sorted({getattr(i, "file_path", "") for i in sa_errors if getattr(i, "file_path", "")})
        if not offending:
            # Test failures can't be attributed per file — roll back all.
            offending = list(modified_files)
        for fp in offending:
            original = originals.get(fp)
            if original is None:
                continue
            try:
                _resolve_target_path(repo_path, fp).write_text(original, errors="replace")
                rolled_back.append(fp)
            except OSError as e:
                _log(f"[verify_and_rollback] rollback failed for {fp}: {e}")

    verification_passed = not rolled_back and not sa_errors and tests_ok

    return {
        "sa_issues": sa_issues,
        "test_result": test_result,
        "verification_passed": verification_passed,
        "rolled_back_files": rolled_back,
    }
