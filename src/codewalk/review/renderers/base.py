"""Base renderer contract."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.codewalk.review.report import ReviewReport


@runtime_checkable
class Renderer(Protocol):
    """A renderer converts a ReviewReport into a presentation format."""

    def render(self, report: ReviewReport) -> str:
        ...
