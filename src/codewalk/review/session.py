"""Internal review session model."""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from src.codewalk.review.report import ReviewContextPackage, ReviewReport


class SessionStatus(str, Enum):
    """Lifecycle status of a review session."""
    ACTIVE = "active"
    COMPLETED = "completed"
    ERROR = "error"
    # Superseded by a newer review before it finished: never completed, never
    # errored — just stale. A new review abandons older active ones so every
    # review starts from a clean slate.
    ABANDONED = "abandoned"


@dataclass
class ReviewSession:
    """Persistent state for one review run."""
    session_id: str
    repo_path: str
    target_branch: str | None
    commit: str | None
    staged: bool
    status: SessionStatus = SessionStatus.ACTIVE
    report: ReviewReport | None = None
    context_package: ReviewContextPackage | None = None
    error: str | None = None
    folder_name: str = ""
    current_branch: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @staticmethod
    def generate_id() -> str:
        return secrets.token_urlsafe(12)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "repo_path": self.repo_path,
            "target_branch": self.target_branch,
            "commit": self.commit,
            "staged": self.staged,
            "status": self.status.value,
            "report": self.report.to_dict() if self.report else None,
            "context_package": self.context_package.to_dict() if self.context_package else None,
            "error": self.error,
            "folder_name": self.folder_name,
            "current_branch": self.current_branch,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
