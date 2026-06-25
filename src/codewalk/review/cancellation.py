"""Cancellation support for long-running review operations.

Both the API and MCP paths can register a review, check whether it has been
cancelled, and abort early. Cancellation is cooperative: the engine checks the
cancellation token at phase boundaries and between LLM calls.
"""
from __future__ import annotations

import threading
from typing import Any


class ReviewCancelledError(Exception):
    """Raised when a review is cancelled mid-flight."""

    def __init__(self, review_id: str) -> None:
        super().__init__(f"Review {review_id} was cancelled")
        self.review_id = review_id


# In-memory registry of active reviews. Maps review_id -> threading.Event.
# An event that is set means the review has been cancelled.
_active_reviews: dict[str, threading.Event] = {}
_registry_lock = threading.Lock()


def start_review(review_id: str) -> None:
    """Register a new active review."""
    with _registry_lock:
        _active_reviews[review_id] = threading.Event()


def end_review(review_id: str) -> None:
    """Remove a review from the active registry."""
    with _registry_lock:
        _active_reviews.pop(review_id, None)


def cancel_review(review_id: str) -> bool:
    """Signal that a review should be cancelled.

    Returns True if the review was found and cancelled, False otherwise.
    """
    with _registry_lock:
        event = _active_reviews.get(review_id)
        if event is None:
            return False
        event.set()
        return True


def is_cancelled(review_id: str | None) -> bool:
    """Return True if the given review has been cancelled."""
    if review_id is None:
        return False
    with _registry_lock:
        event = _active_reviews.get(review_id)
        if event is None:
            return False
        return event.is_set()


def check_cancelled(review_id: str | None) -> None:
    """Raise ReviewCancelledError if the review has been cancelled."""
    if review_id and is_cancelled(review_id):
        raise ReviewCancelledError(review_id)


def get_cancel_event(review_id: str | None) -> threading.Event | None:
    """Return the cancel event for a review, or None."""
    if review_id is None:
        return None
    with _registry_lock:
        return _active_reviews.get(review_id)


def _is_cancelled_event(event: Any | None) -> bool:
    """Return True if the provided object is a set threading.Event."""
    if event is None:
        return False
    return bool(getattr(event, "is_set", lambda: False)())
