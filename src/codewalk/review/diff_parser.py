"""Diff Parser utilities for Codewalk."""
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from src.codewalk.ingestion.scanner import detect_language


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


def get_diff(
        staged: bool = False, target_branch: str | None = None,
        commit: str | None = None, since_commit: str | None = None,
        repo_path: str | None = None
) -> str:
    """Run git diff and return raw unified diff text.

      Args:
          staged: If True, diff staged changes (--staged).
          target_branch: Diff current HEAD against this branch.
          commit: Show diff for a specific commit (SHA or ref like HEAD, HEAD~2).
          since_commit: Diff ``since_commit..HEAD`` for incremental reviews.
          repo_path: Working directory for git command.

      Priority: commit > since_commit > target_branch > staged > unstaged (default).
    """
    cmd = ["git", "diff", "--unified=5"]

    if commit:
        # Show what this specific commit changed (parent → commit).
        # Fall back to git show for root commits that have no parent.
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
        # Diff everything from the baseline commit to the current working tree.
        # This captures both commits made since the baseline and uncommitted edits.
        cmd = ["git", "diff", "--unified=5", since_commit]
    elif staged:
        cmd.append("--staged")
    elif target_branch:
        cmd.append(f"{target_branch}...HEAD")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=repo_path,
    )

    return result.stdout

def get_parsed_diff(diff_text: str) -> list[DiffFile]:
    """Parse raw unified diff text into structured DiffFile objects."""
    from unidiff import PatchSet

    if not diff_text.strip():
        return []
    
    patch = PatchSet(diff_text)
    diff_files = []

    for patched_file in patch:
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

    return diff_files



        
                