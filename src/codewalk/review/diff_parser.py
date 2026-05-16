import subprocess
from pathlib import Path

from src.codewalk.review.models import DiffHunk, DiffFile
from src.codewalk.ingestion.scanner import detect_language


def get_diff(staged: bool = False, target_branch: str | None = None) -> str:
    """Run git diff and return raw unified diff text."""
    cmd = ["git", "diff", "--unified=5"]
    if staged:
        cmd.append("--staged")
    if target_branch:
        cmd.append(f"{target_branch}...HEAD")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
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
                
                lines.append({
                    "line_number": line_no or 0,
                    "content": line.value,
                    "change_type": change_type,
                })
            
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



        
                