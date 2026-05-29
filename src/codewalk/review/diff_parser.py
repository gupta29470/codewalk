import subprocess
from pathlib import Path

from src.codewalk.review.models import DiffHunk, DiffFile, ChangedLine
from src.codewalk.ingestion.scanner import detect_language


def get_diff(
        staged: bool = False, target_branch: str | None = None,
        commit: str | None = None, repo_path: str | None = None
) -> str:
    """Run git diff and return raw unified diff text.

      Args:
          staged: If True, diff staged changes (--staged).
          target_branch: Diff current HEAD against this branch.
          commit: Show diff for a specific commit (SHA or ref like HEAD, HEAD~2).
          repo_path: Working directory for git command.

      Priority: commit > target_branch > staged > unstaged (default).
    """
    cmd = ["git", "diff", "--unified=5"]

    if commit:
        # Show what this specific commit changed (parent → commit)
        cmd = ["git", "diff", "--unified=5", f"{commit}~1", commit]
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



        
                