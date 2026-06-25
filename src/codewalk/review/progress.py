"""Progress reporting for the one-stop review pipeline."""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReviewProgressEvent:
    """A single progress event emitted by the review engine."""

    review_id: str
    phase: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)


class ReviewProgressReporter:
    """Thread-safe progress reporter used by the review engine.

    The streaming endpoint creates a reporter, passes it to ``run_review`` via
    ``asyncio.to_thread``, and reads events from the reporter's queue to push
    SSE messages to the client.
    """

    def __init__(self, review_id: str, maxsize: int = 256):
        self.review_id = review_id
        self._queue: queue.Queue[ReviewProgressEvent | None] = queue.Queue(maxsize=maxsize)

    def report(self, phase: str, message: str, data: dict[str, Any] | None = None) -> None:
        """Emit a progress event. Safe to call from any thread."""
        event = ReviewProgressEvent(
            review_id=self.review_id,
            phase=phase,
            message=message,
            data=data or {},
        )
        self._queue.put_nowait(event)

    def done(self) -> None:
        """Signal that no more events will be produced."""
        self._queue.put_nowait(None)

    def get(self, timeout: float | None = None) -> ReviewProgressEvent | None:
        """Consume the next event. Blocks until one is available."""
        return self._queue.get(timeout=timeout)


class ReviewProgressBus:
    """Global registry of active progress reporters keyed by review_id."""

    def __init__(self):
        self._reporters: dict[str, ReviewProgressReporter] = {}
        self._lock = threading.Lock()

    def register(self, review_id: str, reporter: ReviewProgressReporter) -> None:
        with self._lock:
            self._reporters[review_id] = reporter

    def unregister(self, review_id: str) -> None:
        with self._lock:
            self._reporters.pop(review_id, None)

    def get(self, review_id: str) -> ReviewProgressReporter | None:
        with self._lock:
            return self._reporters.get(review_id)


# Global bus used by the API streaming endpoint and the engine.
review_progress_bus = ReviewProgressBus()
