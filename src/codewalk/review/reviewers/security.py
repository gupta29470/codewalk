"""Security-focused reviewer that runs across all changed files."""
from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from src.codewalk.review.diff_parser import DiffFile
from src.codewalk.review.report import Finding

from .base import BaseReviewer, ReviewContext
from .utils import _build_file_prompt, run_structured_review


_SECURITY_PROMPT = """# Principal Security Engineer — Code Review

You are a principal security engineer reviewing a pull request diff. Your only scope is security: injection vulnerabilities, unsafe deserialization, secret leakage, insecure authentication/authorization, missing access controls, path traversal, SSRF, unsafe eval, XSS, CSRF, insecure cryptography, and privacy/PII exposure.

Do not flag style, architecture, testing, or general code-quality issues. Leave those to the other reviewers.

## Severity
- **critical**: exploitable vulnerability that can lead to data breach, RCE, privilege escalation, or production compromise
- **warning**: plausible security weakness or missing defense in depth
- **suggestion**: minor hardening opportunity

## Output
Return a JSON object with an `issues` array. Each issue must include:
- `severity`: "blocker" | "error" | "suggestion"
- `category`: "security" (or "privacy" / "hygiene" if more specific)
- `file_path`, `line_number`, `title`, `explanation`
- `current_code`: exact snippet from the diff
- `recommended_code`: corrected snippet or null
- `blocking`: true for exploitable or high-risk findings
- `confidence`: "high" | "medium" | "low"

## Rules
- Only flag issues caused or worsened by the diff.
- Provide a concrete fix for every issue.
- `blocking=true` for exploitable vulnerabilities and high-risk weaknesses.
- Do not invent issues. Evidence must be visible in the diff or surrounding context.
- Report each occurrence independently; the engine deduplicates later.
"""


class SecurityReviewer(BaseReviewer):
    """Security reviewer that runs on every changed file."""

    name = "security"

    def can_review(self, file_path: str, language: str) -> bool:
        return True

    def build_prompt(self, context: ReviewContext, language: str | None = None) -> str:
        """Build the security reviewer prompt for one or more files."""
        prompt = _SECURITY_PROMPT

        core = context.rubrics.core
        language_rubric = context.rubrics.for_language(language)
        framework = context.rubrics.framework
        fallback = context.rubrics.fallback

        if core:
            prompt = f"{prompt}\n\n## Core review rubric\n\n{core}"
        if language_rubric:
            prompt = f"{prompt}\n\n## Language-specific security guidance ({language or 'unknown'})\n\n{language_rubric}"
        if framework:
            prompt = f"{prompt}\n\n## Framework-specific security guidance\n\n{framework}"
        if fallback:
            prompt = f"{prompt}\n\n## Fallback guidance\n\n{fallback}"

        return prompt

    def review(
        self,
        diff_file: DiffFile,
        context: ReviewContext,
        llm: BaseChatModel,
    ) -> tuple[list[Finding], int]:
        system_prompt = self.build_prompt(context, diff_file.language)
        user_content = _build_file_prompt(
            diff_file,
            context.repo_path,
            "",  # Rubric now in system message, not user
            context,
        )
        return run_structured_review(llm, user_content, cancel_event=context.cancel_event, system_prompt=system_prompt)
