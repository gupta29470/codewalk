"""Pydantic schemas for structured review output.

These schemas are consumed by `llm.with_structured_output(...)` in the review
pipeline and then converted into the internal `Issue` / `ReviewResult` dataclasses.

The schemas are intentionally permissive: the model often omits verdict/summary
or uses alternative field names (`findings` vs `issues`, `file_path` vs `file`,
etc.). We accept those variants and let the caller supply sensible defaults.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, AliasChoices, field_validator


class ReviewIssueSchema(BaseModel):
    """One review finding as returned by the LLM."""

    severity: str = Field(
        ...,
        description=(
            "Severity of the issue: 'critical' for bugs/security/data loss, "
            "'warning' for logic errors or missing edge cases, "
            "'suggestion' for nice-to-have improvements."
        ),
    )
    confidence: str = Field(
        "high",
        description="Confidence level: 'high', 'medium', or 'low'.",
    )
    category: str = Field(
        ...,
        description=(
            "Category of the issue. One of: bug, security, style, test, "
            "blast_radius, design, naming, complexity, error_handling, "
            "type_safety, architecture, logging, compatibility, privacy, hygiene."
        ),
    )
    file: str = Field(
        default="",
        validation_alias=AliasChoices("file", "file_path"),
        description="File path where the issue occurs.",
    )
    line: int | None = Field(
        None,
        validation_alias=AliasChoices("line", "line_number"),
        description="Line number in the new version of the file.",
    )
    title: str = Field(
        ...,
        description="One-line summary of the issue.",
    )
    explanation: str = Field(
        ...,
        description="Why this is a problem and what can go wrong.",
    )
    suggestion: str | None = Field(
        None,
        description=(
            "Corrected code the developer can apply. Show full fixed line(s)."
        ),
    )
    fix_description: str | None = Field(
        None,
        description="One sentence explaining the fix.",
    )
    code_snippet: str | None = Field(
        None,
        description="The problematic line(s) from the diff.",
    )

    @field_validator("severity", "confidence", "category", mode="before")
    @classmethod
    def _lowercase_enum_fields(cls, value):
        if isinstance(value, str):
            return value.lower()
        return value


class ReviewOutputSchema(BaseModel):
    """Top-level structured output for the full-diff review pass."""

    verdict: str = Field(
        "approve",
        description=(
            "Overall review verdict. One of: approve, approve_with_nits, "
            "request_changes."
        ),
    )
    verdict_reason: str = Field(
        "",
        description="One sentence explaining the verdict.",
    )
    issues: list[ReviewIssueSchema] = Field(
        default_factory=list,
        validation_alias=AliasChoices("issues", "findings"),
        description="List of issues found in the diff.",
    )
    summary: str = Field(
        "",
        description="One paragraph overall assessment with risk level.",
    )


class ReviewSingleFileOutputSchema(BaseModel):
    """Structured output for a single-file focused review pass."""

    issues: list[ReviewIssueSchema] = Field(
        default_factory=list,
        validation_alias=AliasChoices("issues", "findings"),
        description="List of issues found in this file.",
    )
    summary: str | None = Field(
        None,
        description="Optional one-line summary of the file review.",
    )


class CrossFileIssueSchema(ReviewIssueSchema):
    """One cross-file issue, with optional related files for context."""

    related_files: list[str] = Field(
        default_factory=list,
        description="Other files involved in this cross-file issue.",
    )


class CrossFileReviewOutputSchema(BaseModel):
    """Structured output for the cross-file coherence synthesis pass."""

    issues: list[CrossFileIssueSchema] = Field(
        default_factory=list,
        validation_alias=AliasChoices("issues", "findings"),
        description="Cross-file integration issues found across the whole diff.",
    )
    summary: str | None = Field(
        None,
        description="Optional one-line synthesis of cross-file risks.",
    )
