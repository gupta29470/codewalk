"""Benchmark evaluation for one-stop review."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.codewalk.review.engine import run_review
from src.codewalk.review.report import Finding, ReviewReport, Severity


@dataclass
class ExpectedFinding:
    """A gold-standard expected finding."""
    file_path: str
    line_number: int | None
    title_substring: str
    category: str
    severity: str
    blocking: bool


@dataclass
class EvalResult:
    """Result of evaluating a report against a gold set."""
    total_expected: int
    found: int
    missed: list[ExpectedFinding] = field(default_factory=list)
    false_positives: list[Finding] = field(default_factory=list)
    blocker_recall: float = 0.0
    blocker_precision: float = 0.0
    passes_gate: bool = False


def _finding_matches(expected: ExpectedFinding, finding: Finding) -> bool:
    """Check if a report finding matches an expected finding."""
    if expected.file_path != finding.file_path:
        return False
    if expected.line_number is not None and finding.line_number != expected.line_number:
        return False
    if expected.title_substring.lower() not in finding.title.lower():
        return False
    if expected.category.lower() != finding.category.value.lower():
        return False
    return True


def evaluate_report(
    report: ReviewReport,
    expected: list[ExpectedFinding],
) -> EvalResult:
    """Evaluate a ReviewReport against a gold set of expected findings."""
    all_findings = list(report.findings) + list(report.deterministic_findings)

    found_expected: set[int] = set()
    used_findings: set[int] = set()

    for exp_idx, exp in enumerate(expected):
        for find_idx, finding in enumerate(all_findings):
            if find_idx in used_findings:
                continue
            if _finding_matches(exp, finding):
                found_expected.add(exp_idx)
                used_findings.add(find_idx)
                break

    missed = [expected[i] for i in range(len(expected)) if i not in found_expected]
    false_positives = [all_findings[i] for i in range(len(all_findings)) if i not in used_findings]

    expected_blockers = [e for e in expected if e.blocking]
    found_blockers = [e for e in expected if e.blocking and any(_finding_matches(e, f) for f in all_findings)]

    blocker_recall = len(found_blockers) / len(expected_blockers) if expected_blockers else 1.0

    reported_blockers = [f for f in all_findings if f.blocking]
    true_blockers = [f for f in reported_blockers if any(_finding_matches(e, f) for e in expected)]
    blocker_precision = len(true_blockers) / len(reported_blockers) if reported_blockers else 1.0

    passes_gate = blocker_recall >= 0.95 and blocker_precision >= 0.8

    return EvalResult(
        total_expected=len(expected),
        found=len(found_expected),
        missed=missed,
        false_positives=false_positives,
        blocker_recall=blocker_recall,
        blocker_precision=blocker_precision,
        passes_gate=passes_gate,
    )


def run_eval_on_fixture(
    fixture_path: Path,
    expected: list[ExpectedFinding],
) -> EvalResult:
    """Run review on a fixture repo and evaluate against expected findings."""
    report = run_review(repo_path=fixture_path)
    return evaluate_report(report, expected)


def format_eval_result(result: EvalResult) -> str:
    """Human-readable eval summary."""
    lines = [
        f"Expected: {result.total_expected}",
        f"Found: {result.found}",
        f"Missed: {len(result.missed)}",
        f"False positives: {len(result.false_positives)}",
        f"Blocker recall: {result.blocker_recall:.2%}",
        f"Blocker precision: {result.blocker_precision:.2%}",
        f"Passes gate: {result.passes_gate}",
    ]
    if result.missed:
        lines.append("\nMissed findings:")
        for m in result.missed:
            lines.append(f"  - {m.file_path}:{m.line_number} {m.title_substring}")
    return "\n".join(lines)
