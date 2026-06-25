"""Generic reviewer — language-aware fallback for any file."""
from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from src.codewalk.review.diff_parser import DiffFile
from src.codewalk.review.report import Finding

from .base import BaseReviewer, ReviewContext
from .utils import _build_file_prompt, run_structured_review


_GENERIC_PROMPT = """# Principal Software Engineer — Code Review

You are a principal software engineer reviewing a pull request diff. You have full codebase context: the file tree, the diff, deterministic findings, architecture flags, and the dependency-graph blast radius / centrality / cycle data for every changed file. Use the risk context to prioritize high-impact issues.

Find concrete, actionable issues introduced or worsened by the changes. Do not praise. Do not flag style nits unless they indicate a real bug.

## Severity
- **critical**: security vulnerability, crash, data loss, race condition, breaking API change, PII exposure
- **warning**: logic error, missing edge case, unsafe pattern, type issue, untested new business logic
- **suggestion**: readability, naming, minor consistency

## Output
Return a JSON object with an `issues` array. Each issue must include:
- `severity`: "blocker" | "error" | "suggestion"
- `category`: "bug" | "security" | "type_safety" | "architecture" | "error_handling" | "test" | "blast_radius" | "style" | "design" | "naming" | "complexity" | "logging" | "privacy" | "hygiene"
- `file_path`, `line_number`, `title`, `explanation`
- `current_code`: exact snippet from the diff
- `recommended_code`: corrected snippet or null
- `blocking`: true if must fix before merge
- `confidence`: "high" | "medium" | "low"

## Rules
- Only flag issues caused or worsened by the diff.
- Provide a concrete fix for every issue.
- `blocking=true` for critical issues and mandatory warnings.
- Do not invent issues. Do not repeat the same conceptual issue.
- Infer language, framework, architecture, state management, data layer, and testing approach from the file tree and imports.
- Weight findings more heavily when the changed file has high blast radius, is an architectural bottleneck, or participates in a cycle.

## Example output structure

Return valid JSON only. Follow this exact structure:

```json
{
  "issues": [
    {
      "severity": "error",
      "category": "error_handling",
      "file_path": "path/to/file.py",
      "line_number": 42,
      "title": "Short descriptive title",
      "explanation": "Why this is a problem in THIS specific context, not a textbook definition.",
      "current_code": "exact code from the diff",
      "recommended_code": "corrected code or null",
      "blocking": false,
      "confidence": "high"
    }
  ]
}
```
"""


class GenericReviewer(BaseReviewer):
    """Language-aware fallback reviewer for any file type."""

    name = "generic"

    def can_review(self, file_path: str, language: str) -> bool:
        return True

    def build_prompt(self, context: ReviewContext, language: str | None = None) -> str:
        """Build the generic reviewer prompt for one or more files."""
        prompt = _GENERIC_PROMPT

        core = context.rubrics.core
        language_rubric = context.rubrics.for_language(language)
        framework = context.rubrics.framework
        fallback = context.rubrics.fallback

        if core:
            prompt = f"{prompt}\n\n## Core review rubric\n\n{core}"
        if language_rubric:
            prompt = f"{prompt}\n\n## Language-specific guidance ({language or 'unknown'})\n\n{language_rubric}"
        if framework:
            prompt = f"{prompt}\n\n## Framework-specific guidance\n\n{framework}"
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
