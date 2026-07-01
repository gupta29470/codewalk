"""Execution flow graph generator."""
from collections import deque

from src.codewalk.config import get_llm
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# ─── System prompt for execution flow narration ───────────────────────
FLOW_SYSTEM_PROMPT = """You are a codebase onboarding assistant.
Given a focused execution-flow graph of files, write a concise plain-English narration explaining how the code runs, step by step.

Rules:
- Start from the entry-point files on the left.
- Follow dependency arrows left → right.
- One short paragraph or bullet per layer/step.
- Reference actual file names from the graph.
- Do NOT invent function names or logic not present in the input.
- Keep it concise — 5–10 steps at most.
- Use Markdown headings and numbered lists."""

FLOW_HUMAN_PROMPT = """Here is the execution-flow graph for this codebase:

{nodes}

Dependencies (source → target):
{edges}

Total files in graph: {total_files}

Write a short "How this code runs" narration in Markdown. Do NOT include a diagram."""


def _format_nodes(nodes: list[dict]) -> str:
    """Format nodes for the LLM prompt."""
    lines = []
    for node in nodes:
        level = node.get("level", 0)
        lines.append(f"- Level {level}: {node['name']} ({node['full_path']})")
    return "\n".join(lines)


def _format_edges(edges: list[dict]) -> str:
    """Format edges for the LLM prompt."""
    lines = []
    for edge in edges:
        lines.append(f"- {edge['source_name']} → {edge['target_name']}")
    return "\n".join(lines)


def _build_focused_graph(reading_order: dict, deps: dict, max_nodes: int = 50) -> tuple[list[dict], list[dict]]:
    """Build a focused execution-flow subgraph from the reading order and dependency graph.

    Returns:
        (nodes, edges) where nodes include level and position metadata.
    """
    graph = deps.get("graph", {})
    internal = set(graph.keys())

    # Start with files from the reading order (already topologically sorted by importance)
    ordered_files = [item["file"] for item in reading_order.get("order", []) if item["file"] in internal]

    # Limit to the most important files
    if len(ordered_files) > max_nodes:
        ordered_files = ordered_files[:max_nodes]

    node_set = set(ordered_files)

    # Add any immediate internal dependencies that are also internal, up to a cap
    for file in list(ordered_files):
        for target in graph.get(file, []):
            if target in internal and target not in node_set:
                ordered_files.append(target)
                node_set.add(target)
            if len(ordered_files) >= max_nodes:
                break
        if len(ordered_files) >= max_nodes:
            break

    node_set = set(ordered_files)

    # Compute in-degree within the subgraph
    in_degree = {file: 0 for file in ordered_files}
    outgoing = {file: [] for file in ordered_files}

    for source in ordered_files:
        for target in graph.get(source, []):
            if target in node_set:
                outgoing[source].append(target)
                in_degree[target] += 1

    # Topological layering (Kahn's algorithm)
    level = {file: 0 for file in ordered_files}
    queue = deque([file for file in ordered_files if in_degree[file] == 0])

    for file in list(queue):
        level[file] = 0

    processed = set()
    while queue:
        source = queue.popleft()
        processed.add(source)
        for target in outgoing[source]:
            level[target] = max(level[target], level[source] + 1)
            in_degree[target] -= 1
            if in_degree[target] == 0:
                queue.append(target)

    # Any cycle leftovers get assigned a level based on max predecessor
    for file in ordered_files:
        if file not in processed:
            # Find max predecessor level
            pred_level = -1
            for source in ordered_files:
                if file in outgoing[source]:
                    pred_level = max(pred_level, level[source])
            level[file] = max(level[file], pred_level + 1)

    # Build nodes
    nodes = []
    for idx, file in enumerate(ordered_files):
        nodes.append({
            "id": file,
            "name": file.split("/")[-1],
            "full_path": file,
            "level": level[file],
            "position": idx,
        })

    # Build edges
    edges = []
    seen = set()
    for source in ordered_files:
        for target in outgoing[source]:
            key = (source, target)
            if key in seen:
                continue
            seen.add(key)
            edges.append({
                "source": source,
                "target": target,
                "source_name": source.split("/")[-1],
                "target_name": target.split("/")[-1],
                "type": "imports",
            })

    return nodes, edges


def _compute_layout(nodes: list[dict], edges: list[dict]) -> list[dict]:
    """Assign x/y positions for a left-to-right layered flow layout."""
    h_gap = 240
    v_gap = 70

    # Group by level
    levels: dict[int, list[dict]] = {}
    for node in nodes:
        lvl = node.get("level", 0)
        levels.setdefault(lvl, []).append(node)

    # Sort nodes within each level by reading order (already encoded in position)
    for lvl in levels:
        levels[lvl].sort(key=lambda n: n.get("position", 0))

    for lvl, level_nodes in levels.items():
        for idx, node in enumerate(level_nodes):
            node["x"] = lvl * h_gap
            node["y"] = idx * v_gap - (len(level_nodes) - 1) * v_gap / 2

    return nodes


def _generate_narration(nodes: list[dict], edges: list[dict]) -> str:
    """Generate a plain-English narration using the LLM."""
    prompt = ChatPromptTemplate.from_messages([
        ("system", FLOW_SYSTEM_PROMPT),
        ("human", FLOW_HUMAN_PROMPT),
    ])

    llm = get_llm()
    chain = prompt | llm | StrOutputParser()

    result = chain.invoke({
        "nodes": _format_nodes(nodes),
        "edges": _format_edges(edges),
        "total_files": len(nodes),
    })

    # Strip <think>...</think> tags (DeepSeek reasoning models)
    import re
    result = re.sub(r"<think>[\s\S]*?</think>", "", result).strip()
    return result


def generate_execution_flow(reading_order: dict, deps: dict) -> dict:
    """Generate execution flow as a structured layered graph + narration.

    Args:
        reading_order: from generate_reading_order() — {"order": [...], ...}
        deps: from build_dependency_graph() — {"graph": {...}, ...}

    Returns:
        {
            "nodes": [{id, name, full_path, position, level, x, y}, ...],
            "edges": [{source, target, source_name, target_name, type}, ...],
            "narration": "Markdown string",
        }
    """
    nodes, edges = _build_focused_graph(reading_order, deps)
    nodes = _compute_layout(nodes, edges)
    narration = _generate_narration(nodes, edges)

    return {
        "nodes": nodes,
        "edges": edges,
        "narration": narration,
    }
