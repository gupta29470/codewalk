from src.codewalk.config import get_llm
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# ─── System prompt for execution flow ────────────────────────────────
FLOW_SYSTEM_PROMPT = """You are a codebase onboarding assistant.
Given a reading order of files and their dependencies, generate:

1. A Mermaid flowchart showing the execution flow from entry points to outputs.
2. A plain English narration explaining how the code runs, step by step.

Rules:
- Use `graph TD` (top-down) for the Mermaid diagram
- Show only the main execution path, not every file
- Identify entry points (files with no dependents = nothing imports them)
- Entry points are typically: main.py, app.py, pipeline.py, server.py, cli.py
- Keep narration concise — one sentence per step
- Reference actual file names from the reading order"""

FLOW_HUMAN_PROMPT = """Here is the reading order for this codebase:

{reading_order}

Total files: {total_files}

File dependency graph:
{dependency_summary}

Generate:
1. A Mermaid execution flow diagram (```mermaid ... ```)
2. A "How this code runs" narration (numbered steps)"""


def _format_reading_order(orders: list[dict]) -> str:
    """Format reading order for the LLM prompt."""
    lines = []
    for item in orders:
        name = item["file"].split("/")[-1]
        lines.append(f"{item['position']}. {name} — {item['why']}")

    return "\n".join(lines)

def _format_dependency_summary(graph: dict) -> str:
    """Format dependency graph as a readable summary for the LLM."""
    internal = set(graph.keys())
    lines = []

    for file, deps in graph.items():
        name = file.split("/")[-1]
        internal_deps = [dep.split("/")[-1] for dep in deps if dep in internal]

        if internal_deps:
            lines.append(f"{name} → imports: {', '.join(internal_deps)}")
        else:
            lines.append(f"{name} → (no internal imports)")

    return "\n".join(lines)

def generate_execution_flow(reading_order: dict, deps: dict) -> str:
    """Generate execution flow diagram + narration using the LLM.

    Args:
        reading_order: from generate_reading_order() — {"order": [...], ...}
        deps: from build_dependency_graph() — {"graph": {...}, ...}

    Returns:
        String with Mermaid diagram + narration (Markdown formatted).
    """
    prompt = ChatPromptTemplate.from_messages([
        ("system", FLOW_SYSTEM_PROMPT),
        ("human", FLOW_HUMAN_PROMPT),
    ])

    llm = get_llm()
    chain = prompt | llm | StrOutputParser()

    return chain.invoke({
        "reading_order": _format_reading_order(reading_order["order"]),
        "total_files": reading_order["total_files"],
        "dependency_summary": _format_dependency_summary(deps["graph"]),
    })