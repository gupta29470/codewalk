"""
=============================================================================
 diff_parser.py - Git Diff Retrieval and Parsing
=============================================================================

WHAT THIS FILE DOES:
    1. get_diff(): runs `git diff` and returns raw unified diff text
    2. get_parsed_diff(): parses that raw text into structured DiffFile objects

HOW IT WORKS:
    Uses the `unidiff` library to parse unified diff format into objects.
    Each file becomes a DiffFile with hunks, each hunk has typed lines.

WHERE IT'S CALLED:
    - reviewer.py -> prepare_review_context() calls both functions

DEPENDENCIES:
    - unidiff: third-party diff parser
    - scanner.py: detect_language() for file type detection

=============================================================================
"""

import subprocess
from pathlib import Path

from src.codewalk.review.models import DiffHunk, DiffFile, ChangedLine
from src.codewalk.ingestion.scanner import detect_language


def get_diff(staged: bool = False, target_branch: str | None = None, repo_path: str | None = None) -> str:
    """Run git diff and return raw unified diff text.

    Args:
        staged: If True, diff staged changes (--staged)
        target_branch: If set, diff against that branch (branch...HEAD)
        repo_path: Working directory for git command
    """
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
        cwd=repo_path,
    )
    return result.stdout


def get_parsed_diff(diff_text: str) -> list[DiffFile]:
    """Parse raw unified diff text into structured DiffFile objects.

    Uses the `unidiff` library which handles all the @@ parsing.
    Returns empty list if diff is empty.

    EXAMPLE TRACE (2-line change in color.go):
        diff_text = "--- a/color.go\n+++ b/color.go\n@@ -72,3 +72,4 @@\n func (c *Color) Add(...)\n+    // new comment\n ..."

        patch = PatchSet(diff_text)          → 1 patched_file
        patched_file.path                   = "color.go"
        patched_file.is_added_file          = False
        patched_file.added                  = 1
        patched_file.removed                = 0
        hunk.target_start                   = 72
        hunk.target_length                  = 4
        lines = [
            ChangedLine(line_number=72, content="func (c *Color) Add(...)",  change_type="context"),
            ChangedLine(line_number=73, content="    // new comment\n",      change_type="added"),
        ]
        return → [DiffFile(file_path="color.go", language="go", hunks=[DiffHunk(...)], added_lines=1, removed_lines=0)]
    """
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
                # Classify each line
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