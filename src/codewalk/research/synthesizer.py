"""Synthesize parallel research findings into a single coherent report."""
from __future__ import annotations
from dataclasses import dataclass, field
from src.codewalk.research.researcher import SubFindings

SYNTHESIZER_PROMPT = """You are a senior engineer synthesizing research findings.
Produce a structured markdown report answering the original question.

Use this structure:
## Summary
(2-3 sentence answer)

## Key Findings
(bullet points with file citations)

## Code References
(the most important code snippets with file:line)

## Architecture Diagram
(Mermaid flowchart if helpful, else omit)

Original question: {question}

Sub-findings:
{findings}"""

SYNTHESIS_CRITIC_PROMPT = """You are a senior engineer critiquing a research report.
Check for:
- Missing important files or patterns not mentioned
- Claims not backed by specific code citations
- Gaps between the original question and the answer
- Any sub-question left unaddressed

Return a numbered list of specific gaps. Be concise. If the report is complete, say "LGTM"."""

@dataclass
class StructuredReport:
    """Final research report with citations."""
    question: str
    markdown: str
    sources: list[str] = field(default_factory=list)

def merge_findings(state: dict) -> dict:
    """Fan-in node: collect all SubFindings from the results list."""
    findings = state.get("results", [])
    findings.sort(key=lambda f: f.sub_question_id)
    return {"merged_findings": findings}

def synthesize(state: dict) -> dict:
    """Generate node: LLM synthesizes merged findings → StructuredReport."""
    from src.codewalk.config import get_llm

    question = state["question"]
    findings: list[SubFindings] = state.get("merged_findings", [])

    findings_text = "\n\n".join(
        f"### {finding.sub_question_text}\n{finding.findings}"
        for finding in findings
    )

    all_sources = list({source for finding in findings for source in finding.sources})

    llm = get_llm(temperature=0)
    prompt = SYNTHESIZER_PROMPT.format(question=question, findings=findings_text)
    response = llm.invoke(prompt)

    report = StructuredReport(
        question=question,
        markdown=response.content.strip(),
        sources=all_sources,
    )
    return {"report": report}

