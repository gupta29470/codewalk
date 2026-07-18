"""Base contract for specialized reviewers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_core.language_models.chat_models import BaseChatModel

from src.codewalk.review.diff_parser import DiffFile
from src.codewalk.review.static_analysis import RiskAnnotation
from src.codewalk.review.neighborhood import NeighborhoodResult
from src.codewalk.review.report import Finding
from src.codewalk.review.rubric_loader import Rubrics

if TYPE_CHECKING:
    import threading


@dataclass
class ReviewContext:
    """Context shared with every reviewer for a single review run."""

    repo_path: Path
    file_tree: list[str]
    guidelines: str
    user_prompt: str
    prompt_text: str
    rubrics: Rubrics
    risk_annotation: RiskAnnotation | None = None
    neighborhood: NeighborhoodResult | None = None
    previous_findings: list[Finding] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    cancel_event: "threading.Event | None" = None


class BaseReviewer(ABC):
    """Abstract base for a specialized reviewer.

    Each reviewer has a narrow scope, detects issues in a diff file, and returns
    structured findings.  The engine handles deduplication, clustering, ranking,
    and verdict.
    """

    name: str = "base"

    @abstractmethod
    def can_review(self, file_path: str, language: str) -> bool:
        """Return True if this reviewer can handle the given file."""
        ...

    @abstractmethod
    def review(
        self,
        diff_file: DiffFile,
        context: ReviewContext,
        llm: BaseChatModel,
    ) -> tuple[list[Finding], int]:
        """Review a single changed file and return (findings, token_usage)."""
        ...
