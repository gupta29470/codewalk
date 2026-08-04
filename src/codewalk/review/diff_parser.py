"""Diff Parser utilities for Codewalk."""
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from src.codewalk.ingestion.scanner import detect_language
from src.codewalk.review.target import resolve_diff_target_branch

# Untracked file limits
_MAX_UNTRACKED_FILE_SIZE = 1024 * 1024  # 1MB
_BINARY_CHECK_BYTES = 8192              # first 8KB


@dataclass
class ChangedLine:
    """A single line from a unified diff.

    Attributes:
        line_number: Line number in the new version of the file.
        content: The actual text of the line.
        change_type: One of "added", "removed", or "context".
    """
    line_number: int
    content: str
    change_type: str


@dataclass
class DiffHunk:
    """One @@...@@ block — a contiguous section of changes within a file."""
    start_line: int
    end_line: int
    lines: list[ChangedLine] = field(default_factory=list)
    source_start: int = 0      # old file start line
    source_length: int = 0     # old file line count


@dataclass
class DiffFile:
    """One changed file in the diff."""
    file_path: str
    language: str
    hunks: list[DiffHunk] = field(default_factory=list)
    is_new_file: bool = False
    is_deleted: bool = False
    added_lines: int = 0
    removed_lines: int = 0


def _has_head(repo_path: str | None) -> bool:
    """Return True if the repo has at least one commit (HEAD exists)."""
    return subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        timeout=10,
    ).returncode == 0


def _merge_base(repo_path: str | None, target: str) -> str | None:
    """Return the merge-base of HEAD and target, or None if unavailable."""
    result = subprocess.run(
        ["git", "merge-base", target, "HEAD"],
        cwd=repo_path,
        capture_output=True,
        timeout=10,
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip() or None


def _synthetic_untracked_diff(repo_path: str | None) -> str:
    """Generate unified-diff text for untracked files (new files not yet staged).

    Runs ``git ls-files --others --exclude-standard`` and builds a synthetic
    diff for each eligible file so that ``get_parsed_diff()`` can parse them
    like any other diff hunk.

    Skips binary files, symlinks, files > 512 KB, and files that fail UTF-8 decode.
    """
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files", "--others", "--exclude-standard"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return ""

    parts: list[str] = []
    base = Path(repo_path) if repo_path else Path.cwd()

    for rel_path in result.stdout.strip().splitlines():
        rel_path = rel_path.strip()
        if not rel_path:
            continue

        full_path = base / rel_path

        # Skip symlinks and non-files
        if not full_path.is_file() or full_path.is_symlink():
            continue

        # Skip large files
        try:
            size = full_path.stat().st_size
        except OSError:
            continue
        if size > _MAX_UNTRACKED_FILE_SIZE:
            continue

        # Skip binary files (null byte in first 8KB)
        try:
            with open(full_path, "rb") as f:
                head = f.read(_BINARY_CHECK_BYTES)
            if b"\x00" in head:
                continue
        except OSError:
            continue

        # Read as UTF-8
        try:
            content = full_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        lines = content.splitlines()
        line_count = len(lines)
        if line_count == 0:
            continue

        # Build standard unified diff
        diff_lines = [
            f"diff --git a/{rel_path} b/{rel_path}",
            "new file mode 100644",
            "--- /dev/null",
            f"+++ b/{rel_path}",
            f"@@ -0,0 +1,{line_count} @@",
        ]
        for line in lines:
            diff_lines.append(f"+{line}")

        parts.append("\n".join(diff_lines))

    if not parts:
        return ""
    return "\n" + "\n".join(parts) + "\n"


def get_diff(
        staged: bool = False, target_branch: str | None = None,
        commit: str | None = None, since_commit: str | None = None,
        repo_path: str | None = None
) -> str:
    """Run git diff and return raw unified diff text.

    Returns ALL changes by default — staged, unstaged, and untracked files.
    The only narrow mode is ``staged=True`` (staged changes only).

    Args:
        staged: If True, diff only staged changes (--staged). No untracked files.
        target_branch: Diff from the merge-base of this base and HEAD through
            the working tree — commits on the current branch since it diverged,
            plus uncommitted changes, with untracked files appended. Pass
            ``"current"`` (or an alias) for local changes on this branch only
            (same as omitting ``target_branch``).
        commit: Show diff for a specific commit (SHA or ref). No untracked files.
        since_commit: Diff from ``since_commit`` to working tree + untracked.
        repo_path: Working directory for git command.

    Priority: commit > since_commit > staged > target_branch > default.
    """
    cmd = ["git", "diff", "--unified=5"]
    append_untracked = True
    resolved_target = resolve_diff_target_branch(target_branch)

    if commit:
        # Historical snapshot — no untracked files.
        append_untracked = False
        has_parent = subprocess.run(
            ["git", "rev-parse", "--verify", f"{commit}~1"],
            cwd=repo_path,
            capture_output=True,
            timeout=10,
        ).returncode == 0
        if has_parent:
            cmd = ["git", "diff", "--unified=5", f"{commit}~1", commit]
        else:
            cmd = ["git", "show", "--format=", "-p", commit]
    elif since_commit:
        # Diff everything from the baseline to working tree.
        cmd = ["git", "diff", "--unified=5", since_commit]
    elif staged:
        # Explicit narrow mode — staged only, no untracked.
        append_untracked = False
        cmd.append("--staged")
    elif resolved_target:
        # Diff from the merge-base through the working tree: commits on the
        # current branch since it diverged from the base, plus uncommitted
        # edits, without picking up newer commits on the base itself.
        # Untracked files are appended below.
        base = _merge_base(repo_path, resolved_target) or resolved_target
        cmd.append(base)
    else:
        # Default: all local changes (staged + unstaged) vs last commit.
        if _has_head(repo_path):
            cmd.append("HEAD")
        else:
            # Empty repo / first commit — show staged files.
            cmd.append("--cached")

    result = subprocess.run(
        cmd,
        capture_output=True,
        timeout=60,
        cwd=repo_path,
    )

    # Decode with errors="replace" so binary file content in diffs doesn't crash.
    # Git may include raw bytes when diffing deleted binary files.
    diff_output = result.stdout.decode("utf-8", errors="replace")

    if append_untracked:
        diff_output += _synthetic_untracked_diff(repo_path)

    return diff_output

def get_parsed_diff(diff_text: str) -> list[DiffFile]:
    """Parse raw unified diff text into structured DiffFile objects.

    Skips files whose diff content contains replacement characters (binary
    content that survived UTF-8 decode with errors='replace').
    """
    from unidiff import PatchSet

    if not diff_text.strip():
        return []
    
    patch = PatchSet(diff_text)
    diff_files = []
    skipped: list[str] = []

    for patched_file in patch:
        # Skip binary files — their content will have replacement chars from decode
        if patched_file.is_binary_file:
            skipped.append(patched_file.path)
            continue

        # Also skip if any hunk line contains the replacement character (binary leaked through)
        is_binary_content = False
        for hunk in patched_file:
            for line in hunk:
                if "\ufffd" in line.value:
                    is_binary_content = True
                    break
            if is_binary_content:
                break
        if is_binary_content:
            skipped.append(patched_file.path)
            continue

        hunks = []
        for hunk in patched_file:
            lines = []
            for line in hunk:
                if line.is_added:
                    change_type = "added"
                    line_no = line.target_line_no
                elif line.is_removed:
                    change_type = "removed"
                    line_no = line.source_line_no
                else:
                    change_type = "context"
                    line_no = line.target_line_no
                
                lines.append(ChangedLine(
                    line_number=line_no or 0,
                    content=line.value,
                    change_type=change_type,
                ))
            
            hunks.append(DiffHunk(
                start_line=hunk.target_start,
                end_line=hunk.target_start + hunk.target_length,
                lines=lines,
                source_start=hunk.source_start,
                source_length=hunk.source_length,
            ))

        diff_files.append(DiffFile(
            file_path=patched_file.path,
            language=detect_language(Path(patched_file.path)),
            hunks=hunks,
            is_new_file=patched_file.is_added_file,
            is_deleted=patched_file.is_removed_file,
            added_lines=patched_file.added,
            removed_lines=patched_file.removed,
        ))

    if skipped:
        from src.codewalk.log import log as _log
        _log(f"[diff_parser] Skipped {len(skipped)} binary/non-UTF-8 file(s): {skipped}")

    return diff_files
