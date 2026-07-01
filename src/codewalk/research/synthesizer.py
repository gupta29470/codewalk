"""Synthesize parallel research findings into a single coherent report."""
from __future__ import annotations
from dataclasses import dataclass, field

from src.codewalk.research.researcher import SubFindings
from src.codewalk.graph.graph_store import GraphStore

SYNTHESIZER_PROMPT = """You are a senior engineer synthesizing research findings.
Produce a structured markdown report answering the original question.

Use this structure:
## Summary
(2-3 sentence answer)

## Key Findings
(bullet points with file citations)

## Code References
(the most important code snippets with file:line)

## Architecture Flow
(2-4 short paragraphs explaining how the relevant components connect and flow.
Use the grounded code graph below — file imports and symbol calls — to describe
what calls what and how data moves. Do NOT include diagrams or Mermaid syntax.)

Original question: {question}

Sub-findings:
{findings}

{graph_context}"""

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


def make_synthesizer(graph_store: GraphStore | None = None):
    """Factory for the synthesis LangGraph node.

    Closes over graph_store so the synthesizer can include grounded architecture
    context in the report.
    """
    from src.codewalk.config import get_llm
    from src.codewalk.research.diagram_generator import generate_research_graph_context

    def synthesize(state: dict) -> dict:
        """Generate node: LLM synthesizes merged findings → StructuredReport."""
        question = state["question"]
        findings: list[SubFindings] = state.get("merged_findings", [])

        findings_text = "\n\n".join(
            f"### {finding.sub_question_text}\n{finding.findings}"
            for finding in findings
        )

        all_sources = list({source for finding in findings for source in finding.sources})

        graph_context = ""
        if graph_store and all_sources:
            graph_context = generate_research_graph_context(all_sources, graph_store)
            if graph_context:
                graph_context = f"Grounded code graph for the architecture flow:\n{graph_context}"

        llm = get_llm(temperature=0)
        prompt = SYNTHESIZER_PROMPT.format(
            question=question,
            findings=findings_text,
            graph_context=graph_context,
        )
        response = llm.invoke(prompt)

        report = StructuredReport(
            question=question,
            markdown=response.content.strip(),
            sources=all_sources,
        )
        return {"report": report}

    return synthesize
