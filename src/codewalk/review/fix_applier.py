from __future__ import annotations
import os
from pathlib import Path

def apply_fix_to_file(repo_path: str, file_path: str, old_code: str, new_code: str) -> dict:
    """Apply a single fix by exact text replacement.

    Safety rules:
      - old_code must exist exactly once in the file
      - File must be inside repo_path (path traversal protection)
      - Write is atomic (temp file + rename)

    Returns {"ok": True, "message": "..."} or {"ok": False, "error": "..."}
    """
    # Resolve full path and enforce repo boundary
    full_path = (
        Path(repo_path)/file_path
        if not os.path.isabs(file_path)
        else Path(file_path)
    )

    # Path traversal guard: file must be inside repo
    try:
        full_path.relative_to(Path(repo_path).resolve())
    except ValueError:
        return {"ok": False, "error": f"Path traversal blocked: {file_path}"}
    
    if not full_path.exists():
        return {"ok": False, "error": f"File not found: {file_path}"}
    
    try:
        content = full_path.read_text(errors="replace")
    except OSError as e:
        return {"ok": False, "error": f"Cannot read file: {e}"}
    
    # Exact match check
    count = content.count(old_code)
    if count == 0:
        return {
            "ok": False,
            "error": (
                f"Could not find the specified code in {file_path}.\n\n"
                f"The old_code does not match the file content exactly."
            ),
        }
    if count > 1:
        return {
            "ok": False,
            "error": (
                f"Ambiguous replacement: old_code appears {count} times in {file_path}.\n\n"
                f"Provide more surrounding context (2-3 extra lines) to make it unique."
            ),
        }

    new_content = content.replace(old_code, new_code, 1)

    # Atomic write: temp file → rename
    temp_path = full_path.with_suffix(full_path.suffix + ".tmp")
    try:
        temp_path.write_text(new_content, errors="replace")
        temp_path.replace(full_path)
    except OSError as e:
        return {"ok": False, "error": f"Cannot write file: {e}"}
    
    return {
        "ok": True,
        "message": f"Fix applied to {file_path}",
        "file_path": file_path,
        "old_code": old_code,
        "new_code": new_code,
    }


def apply_fixes_batch(repo_path: str, fixes: list[dict]) -> dict:
    """Apply multiple fixes sequentially. Stop on first error.

    Args:
        repo_path: Root of the repository
        fixes:     List of {"file_path": str, "old_code": str, "new_code": str}

    Returns:
        {
          "applied": [list of successful fixes],
          "failed":  {fix_index: error_message} or null,
          "total":   N
        }
    """
    applied = []
    for index, fix in enumerate(fixes):
        result = apply_fix_to_file(
            repo_path,
            fix["file_path"],
            fix["old_code"],
            fix["new_code"],
        )
        if not result["ok"]:
            return {
                "applied": applied,
                "failed": {"index": index, **result},
                "total": len(fixes),
            }
        applied.append(result)

    return {"applied": applied, "failed": None, "total": len(fixes)}
