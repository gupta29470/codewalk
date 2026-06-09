from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Severity(Enum):
    CRITICAL = "critical"       # 🔴 Bugs, security vulnerabilities, data loss
    WARNING = "warning"         # 🟡 Logic errors, missing edge cases
    SUGGESTION = "suggestion"   # 🟢 Nice-to-have improvements

class Category(Enum):
    BUG = "bug"
    SECURITY = "security"
    STYLE = "style"
    TEST = "test"
    BLAST_RADIUS = "blast_radius"
    DESIGN = "design"
    NAMING = "naming"
    COMPLEXITY = "complexity"
    ERROR_HANDLING = "error_handling"
    TYPE_SAFETY = "type_safety"
    ARCHITECTURE = "architecture"
    LOGGING = "logging"
    COMPATIBILITY = "compatibility"
    PRIVACY = "privacy"
    HYGIENE = "hygiene"

class Verdict(Enum):
    APPROVE = "approve"
    APPROVE_WITH_NITS = "approve_with_nits"
    REQUEST_CHANGES = "request_changes"

class Confidence(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

@dataclass
class Issue:
    """A single review finding."""
    severity: Severity
    category: Category
    file_path: str
    line_number: int | None
    title: str
    explanation: str
    confidence: Confidence = Confidence.HIGH
    suggestion: str | None = None
    fix_description: str | None = None
    code_snippet: str | None = None

@dataclass
class ReviewResult:
    """Final output of a code review."""
    issues: list[Issue] = field(default_factory=list)
    summary: str = ""
    verdict: Verdict = Verdict.APPROVE
    verdict_reason: str = ""
    files_reviewed: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    diff_text: str = ""  # raw diff — used by reflect_on_review() at the call site

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
