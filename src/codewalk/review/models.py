"""
=============================================================================
 models.py - Data Models for Code Review
=============================================================================

WHAT THIS FILE DOES:
    Defines all the dataclasses used by the review system:
    - Issue: a single finding (bug, security issue, suggestion)
    - ReviewResult: the final output of a review
    - ChangedLine/DiffHunk/DiffFile: structured representation of git diffs

WHY DATACLASSES:
    These are pure data containers - no logic, just fields.
    They provide type safety and clear contracts between review components.

=============================================================================
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


# --- Severity and Category Enums ---

class Severity(Enum):
    """How bad is the issue?"""
    CRITICAL = "critical"       # Bugs, security vulnerabilities, data loss
    WARNING = "warning"         # Logic errors, missing edge cases
    SUGGESTION = "suggestion"   # Nice-to-have improvements


class Category(Enum):
    """What kind of issue is it?"""
    BUG = "bug"
    SECURITY = "security"
    STYLE = "style"
    TEST = "test"
    BLAST_RADIUS = "blast_radius"


# --- Review Output Models ---

@dataclass
class Issue:
    """A single review finding."""
    severity: Severity
    category: Category
    file_path: str
    line_number: int | None
    title: str
    explanation: str
    suggestion: str | None = None
    code_snippet: str | None = None


@dataclass
class ReviewResult:
    """Final output of a code review - aggregates all issues + stats."""
    issues: list[Issue] = field(default_factory=list)
    summary: str = ""
    files_reviewed: int = 0
    lines_added: int = 0
    lines_removed: int = 0


# --- Diff Parsing Models ---

@dataclass
class ChangedLine:
    """A single line from a unified diff."""
    line_number: int        # Line number in new version
    content: str            # The actual text
    change_type: str        # "added", "removed", or "context"


@dataclass
class DiffHunk:
    """One @@ block - a contiguous section of changes within a file."""
    start_line: int
    end_line: int
    lines: list[ChangedLine] = field(default_factory=list)


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