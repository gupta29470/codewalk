import json
from src.codewalk.config import get_llm
from src.codewalk.review.diff_parser import get_diff, get_parsed_diff
from src.codewalk.review.models import ReviewResult, Issue, Severity, Category
from src.codewalk.review.test_coverage import TestCoverage
from src.codewalk.review.guidelines_loader import get_guidelines_store, search_guidelines
from src.codewalk.review.review_prompts import REVIEW_SYSTEM_PROMPT, REVIEW_USER_PROMPT

def review_diff(
    staged: bool = False,
    target_branch: str | None = None,
    use_llm: bool = True,
    store=None,                  # VectorStore — codebase pattern context
    deps: dict | None = None,   # dependency graph — blast radius
)-> ReviewResult:
    """Main review pipeline: git diff → checks → LLM → ReviewResult."""
    # ── Step 1: Get diff ──
    diff_text = get_diff(staged=staged, target_branch=target_branch)
    if not diff_text.strip():
        return ReviewResult(summary="No changes to review.")
    
    # ── Step 2: Parse diff ──
    diff_files = get_parsed_diff(diff_text)

    # ── Step 3: Test coverage check (no LLM needed) ──
    pre_check_issues = []
    pre_check_issues.extend(TestCoverage().analyze(diff_files))

    # ── Step 4: Blast radius context (REUSE from Codewalk) ──
    blast_context = ""
    if deps:
        from src.codewalk.analysis.blast_radius import get_blast_radius
        high_risk = []
        for diff_file in diff_files:
            radius = get_blast_radius(diff_file.file_path, deps)
            if radius["risk_level"] in ("high", "critical"):
                high_risk.append(
                    f"⚠️ {diff_file.file_path} — {radius['risk_level'].upper()} risk, "
                    f"{radius['affected_files']} dependents: "
                    f"{', '.join(radius['direct'][:5])}"
                )

        if high_risk:
            blast_context = (
                "## Blast Radius Warnings\n"
                "These changed files have many dependents — high-risk:\n"
                + "\n".join(high_risk)
            )
    
    # ── Step 5: Codebase pattern context (REUSE vector store) ──
    pattern_context = ""
    if store:
        from src.codewalk.rag.chain import format_context
        file_names = [df.file_path for df in diff_files[:3]]
        query = f"code patterns and conventions in {', '.join(file_names)}"
        results = store.search(query, n_results=3)
        if results:
            pattern_context = (
                "## Existing Codebase Patterns\n"
                "Similar code elsewhere in this project:\n"
                + format_context(results)
            )

    # ── Step 6: Team guidelines RAG
    guidelines_context = ""
    guidelines_store = get_guidelines_store()
    if guidelines_store:
        guidelines_context = search_guidelines(
            guidelines_store, diff_files, n_results=3
        )
    
    # ── Step 7: LLM review (security + bugs + style — ALL languages) ──
    llm_issues = []
    llm_summary = ""
    if use_llm:
        llm = get_llm(temperature=0)

        pre_check_str = "\n".join(
            f"- [{issue.severity.value}] {issue.file_path}:{issue.line_number} — {issue.title}"
            for issue in pre_check_issues
        ) or "None found."

        system = REVIEW_SYSTEM_PROMPT.format(
            blast_radius_context=blast_context,
            codebase_patterns=pattern_context,
            team_guidelines=guidelines_context,
        )

        user = REVIEW_USER_PROMPT.format(
            diff_content=diff_text[:15000],  # cap to avoid token limits
            pre_checks=pre_check_str,
        )

        response = llm.invoke([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ])

        # Parse LLM JSON response
        try:
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            parsed = json.loads(content)
            llm_summary = parsed.get("summary", "")

            category_map = {
                "bug": Category.BUG,
                "security": Category.SECURITY,
                "style": Category.STYLE,
            }

            for issue in parsed.get("issues", []):
                llm_issues.append(Issue(
                    severity=Severity[issue["severity"].upper()],
                    category=category_map.get(
                        issue.get("category", "bug"), Category.BUG
                    ),
                    file_path=issue.get("file", "unknown"),
                    line_number=issue.get("line"),
                    title=issue.get("title", ""),
                    explanation=issue.get("explanation", ""),
                    suggestion=issue.get("suggestion"),
                ))
        except (json.JSONDecodeError, KeyError, IndexError):
            llm_summary = response.content  # fallback to raw text

    # ── Step 8: Merge and return ──
    all_issues = pre_check_issues + llm_issues
    total_added = sum(df.added_lines for df in diff_files)
    total_removed = sum(df.removed_lines for df in diff_files)

    return ReviewResult(
        issues=all_issues,
        summary=llm_summary or f"Reviewed {len(diff_files)} files. "
                                f"Found {len(all_issues)} issues.",
        files_reviewed=len(diff_files),
        lines_added=total_added,
        lines_removed=total_removed,
    )


