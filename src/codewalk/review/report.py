"""Review report data model for the simplified one-stop review flow."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from src.codewalk.review.rubric_loader import Rubrics


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _extract_function_or_class_anchor(snippet: str | None) -> str | None:
    """Best-effort extraction of enclosing function/class name from code snippets."""
    if not snippet:
        return None

    patterns = [
        r"^\s*(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        r"^\s*class\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        r"^\s*(?:public|private|protected|static|async)?\s*(?:function\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\(",
        r"^\s*(?:void|int|String|bool|Future|Widget|[a-zA-Z_][a-zA-Z0-9_<>,\[\]]*)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(",
    ]
    for line in snippet.splitlines():
        for pattern in patterns:
            match = re.search(pattern, line)
            if match:
                return match.group(1)
    return None


def _semantic_anchor(finding: Finding) -> str:
    """Return a stable semantic anchor for a finding.

    Tries to identify the enclosing function/class or API call pattern so that
    the same bug survives renames and line-number shifts.
    """
    snippets = [finding.current_code]
    for ev in finding.evidence or []:
        snippets.append(ev.get("snippet"))

    for snippet in snippets:
        anchor = _extract_function_or_class_anchor(snippet)
        if anchor:
            return anchor.lower()

    title = _normalize(finding.title)
    title = re.sub(r"\b[a-z_][a-z0-9_]{0,2}\b", "", title)
    title = re.sub(r"\d+", "", title)
    return "|".join([_normalize(title), finding.file_path])


def _compute_finding_id(finding: Finding) -> str:
    """Compute a stable ID for a finding using semantic anchors + fuzzy inputs."""
    anchor = _semantic_anchor(finding)
    key = "|".join([
        finding.category.value,
        finding.file_path,
        anchor,
    ])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


class Severity(str, Enum):
    """How serious a review finding is."""
    BLOCKER = "blocker"
    ERROR = "error"
    SUGGESTION = "suggestion"


class Category(str, Enum):
    """What kind of issue was found."""
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
    PRIVACY = "privacy"
    HYGIENE = "hygiene"


class Confidence(str, Enum):
    """How confident the reviewer is in a finding."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Source(str, Enum):
    """Where a finding originated."""
    DETERMINISTIC = "deterministic"
    LLM = "llm"
    VERIFICATION = "verification"


class Pillar(str, Enum):
    """Review pillar used to categorize findings."""
    TYPE_SAFETY_ARCHITECTURE = "type_safety_architecture"
    EDGE_CASES_RUNTIME_SAFETY = "edge_cases_runtime_safety"
    IDIOMS_CLEAN_CODE = "idioms_clean_code"
    TESTS_COVERAGE = "tests_coverage"
    SECURITY_BOUNDARIES = "security_boundaries"


@dataclass
class Finding:
    """A single issue produced by the review pipeline."""

    severity: Severity
    category: Category
    file_path: str
    line_number: int | None
    title: str
    explanation: str
    current_code: str | None = None
    recommended_code: str | None = None
    blocking: bool = False
    confidence: Confidence = Confidence.HIGH
    source: Source = Source.LLM
    pillar: Pillar | None = None
    subcategory: str | None = None
    id: str = field(default="")
    evidence: list[dict[str, Any]] = field(default_factory=list)
    verifier_notes: str | None = None
    status: str = "new"  # new | still_present
    user_verdict: str | None = None  # null | "accepted" | "rejected"
    verdict_at: str | None = None  # ISO timestamp of user verdict

    def __post_init__(self) -> None:
        if not self.id:
            self.id = _compute_finding_id(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity.value,
            "category": self.category.value,
            "subcategory": self.subcategory,
            "pillar": self.pillar.value if self.pillar else None,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "title": self.title,
            "explanation": self.explanation,
            "current_code": self.current_code,
            "recommended_code": self.recommended_code,
            "blocking": self.blocking,
            "confidence": self.confidence.value,
            "source": self.source.value,
            "evidence": self.evidence,
            "verifier_notes": self.verifier_notes,
            "status": self.status,
            "user_verdict": self.user_verdict,
            "verdict_at": self.verdict_at,
        }


@dataclass
class ArchitectureFlags:
    """Architecture risk signals attached to a review (bottlenecks, cycles touched)."""
    bottlenecks_touched: list[str] = field(default_factory=list)
    cycles_touched: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bottlenecks_touched": self.bottlenecks_touched,
            "cycles_touched": self.cycles_touched,
        }


@dataclass
class ReviewReport:
    """Final output of run_review: raw findings + metadata.

    Mirrors the MCP design: ``deterministic_findings`` are produced first by
    static analysis / graph metrics, then ``findings`` are produced by the LLM.
    """

    findings: list[Finding] = field(default_factory=list)
    deterministic_findings: list[Finding] = field(default_factory=list)
    architecture_flags: ArchitectureFlags = field(default_factory=ArchitectureFlags)
    files_reviewed: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    token_usage: int = 0
    time_seconds: float = 0.0
    session_id: str = ""
    folder_name: str = ""
    schema_version: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "issues": [f.to_dict() for f in self.findings],
            "static_issues": [f.to_dict() for f in self.deterministic_findings],
            "architecture_flags": self.architecture_flags.to_dict(),
            "files_reviewed": self.files_reviewed,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
            "token_usage": self.token_usage,
            "time_seconds": self.time_seconds,
            "session_id": self.session_id,
            "folder_name": self.folder_name,
        }


@dataclass
class ReviewContextPackage:
    """Raw review context for the host LLM (MCP path)."""

    repo_path: Path
    target_branch: str | None
    commit: str | None
    staged: bool
    diff_files: list[Any] = field(default_factory=list)
    deterministic_findings: list[Finding] = field(default_factory=list)
    neighborhood_snippets: list[Any] = field(default_factory=list)
    architecture_flags: ArchitectureFlags = field(default_factory=ArchitectureFlags)
    file_tree: list[str] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    risk_summary_lines: list[str] = field(default_factory=list)
    prompt_core: str = ""
    prompt_language: str = ""
    prompt_framework: str = ""
    prompt_custom: str = ""
    prompt_fallback: str = ""
    user_prompt: str = ""
    rubrics: Rubrics = field(default_factory=Rubrics)
    session_id: str = ""
    folder_name: str = ""
    current_branch: str | None = None
    files_reviewed: int = 0
    lines_added: int = 0
    lines_removed: int = 0

    @property
    def findings(self) -> list[Finding]:
        """All deterministic findings."""
        return list(self.deterministic_findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_path": str(self.repo_path),
            "target_branch": self.target_branch,
            "commit": self.commit,
            "staged": self.staged,
            "files_reviewed": self.files_reviewed,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
            "architecture_flags": self.architecture_flags.to_dict(),
            "findings": [f.to_dict() for f in self.findings],
            "deterministic_findings": [f.to_dict() for f in self.deterministic_findings],
            "file_tree": self.file_tree,
            "affected_files": self.affected_files,
            "risk_summary_lines": self.risk_summary_lines,
            "prompt_core": self.prompt_core,
            "prompt_language": self.prompt_language,
            "prompt_framework": self.prompt_framework,
            "prompt_custom": self.prompt_custom,
            "prompt_fallback": self.prompt_fallback,
            "user_prompt": self.user_prompt,
            "session_id": self.session_id,
            "folder_name": self.folder_name,
            "current_branch": self.current_branch,
        }

    def to_markdown(self) -> str:
        """Render as markdown for the MCP host LLM."""
        from src.codewalk.review.renderers import render_review_context

        return render_review_context(self)
