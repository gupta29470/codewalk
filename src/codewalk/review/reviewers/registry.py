"""Reviewer registry and dispatch."""
from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from src.codewalk.review.diff_parser import DiffFile
from src.codewalk.review.report import Finding

from .base import BaseReviewer, ReviewContext
from .generic import GenericReviewer
from .security import SecurityReviewer
from .utils import run_batch_review


DEFAULT_REVIEWERS: list[type[BaseReviewer]] = [
    GenericReviewer,
    SecurityReviewer,
]


class ReviewerRegistry:
    """Collects reviewers and dispatches them to diff files."""

    def __init__(self, reviewer_classes: list[type[BaseReviewer]] | None = None):
        self.reviewers: list[BaseReviewer] = [
            cls() for cls in (reviewer_classes or DEFAULT_REVIEWERS)
        ]

    def select_for(self, diff_file: DiffFile) -> list[BaseReviewer]:
        """Return reviewers that should run on this file."""
        selected: list[BaseReviewer] = []

        # Generic reviewer handles all languages using rubrics from context.
        for reviewer in self.reviewers:
            if reviewer.name == "generic" and reviewer.can_review(
                diff_file.file_path, diff_file.language
            ):
                selected.append(reviewer)
                break

        # Security runs in addition to the generic reviewer.
        for reviewer in self.reviewers:
            if reviewer.name == "security" and reviewer.can_review(
                diff_file.file_path, diff_file.language
            ):
                selected.append(reviewer)
                break

        return selected

    def review_file(
        self,
        diff_file: DiffFile,
        context: ReviewContext,
        llm: BaseChatModel,
    ) -> tuple[list[Finding], int]:
        """Run all applicable reviewers on a single file.

        Returns (findings, token_usage).
        """
        findings: list[Finding] = []
        token_usage = 0
        for reviewer in self.select_for(diff_file):
            try:
                file_findings, file_tokens = reviewer.review(diff_file, context, llm)
                findings.extend(file_findings)
                token_usage += file_tokens
            except Exception as e:
                # Log and continue so one reviewer failure doesn't kill the review.
                import logging

                logger = logging.getLogger("codewalk")
                logger.warning(f"[reviewers] {reviewer.name} failed for {diff_file.file_path}: {e}")
        return findings, token_usage

    def review_batch(
        self,
        diff_files: list[DiffFile],
        context: ReviewContext,
        llm: BaseChatModel,
    ) -> tuple[list[Finding], int]:
        """Run all applicable reviewers on a batch of files in one LLM call each.

        Returns (findings, token_usage).
        """
        findings: list[Finding] = []
        token_usage = 0

        # Determine dominant language for rubric selection in the batch prompt.
        from collections import Counter
        lang_counts = Counter(df.language for df in diff_files if df.language)
        dominant_language = lang_counts.most_common(1)[0][0] if lang_counts else None

        # Generic reviewer runs once over the whole batch.
        generic = next((r for r in self.reviewers if r.name == "generic"), None)
        if generic and all(generic.can_review(df.file_path, df.language) for df in diff_files):
            try:
                prompt = generic.build_prompt(context, dominant_language)
                batch_findings, batch_tokens = run_batch_review(
                    llm, diff_files, context, prompt, cancel_event=context.cancel_event
                )
                findings.extend(batch_findings)
                token_usage += batch_tokens
            except Exception as e:
                import logging

                logger = logging.getLogger("codewalk")
                logger.warning(f"[reviewers] generic batch review failed: {e}")

        # Security reviewer runs once over the whole batch.
        security = next((r for r in self.reviewers if r.name == "security"), None)
        if security and all(security.can_review(df.file_path, df.language) for df in diff_files):
            try:
                prompt = security.build_prompt(context, dominant_language)
                batch_findings, batch_tokens = run_batch_review(
                    llm, diff_files, context, prompt, cancel_event=context.cancel_event
                )
                findings.extend(batch_findings)
                token_usage += batch_tokens
            except Exception as e:
                import logging

                logger = logging.getLogger("codewalk")
                logger.warning(f"[reviewers] security batch review failed: {e}")

        return findings, token_usage
