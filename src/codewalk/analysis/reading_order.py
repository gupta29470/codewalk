"""
=============================================================================
 reading_order.py - Generate Optimal File Reading Order for Onboarding
=============================================================================

WHAT THIS FILE DOES:
    Given a dependency graph, produces an ordered list of files that a new
    developer should read to understand the codebase. Files are sorted so
    that dependencies come BEFORE the files that use them.

    Example output:
        1. config.py (no dependencies - read this first)
        2. log.py (depends on config.py - you already read that)
        3. scanner.py (depends on config.py, log.py - both done)
        4. pipeline.py (depends on scanner, embedder - read those first)

HOW IT WORKS:
    1. Topological sort of the file dependency graph
       (dependencies before dependents - like a build order)
    2. Add "why" explanations for each file's position
    3. Optionally tag files as "must-read" / "optional" / "skip" using LLM

REAL-WORLD ANALOGY:
    Like a course prerequisite system:
        - "Data Structures" has no prerequisites - take first
        - "Algorithms" requires "Data Structures" - take second
        - "Machine Learning" requires "Algorithms" + "Statistics" - take after both
    Files are courses, imports are prerequisites.

WHY TOPOLOGICAL SORT?
    Guarantees you NEVER read a file before reading its dependencies.
    Without this, you'd be reading pipeline.py and encountering:
    "What is VectorStore? What is scan_repository?" - frustrating!
    With toposort: you already read vector_store.py and scanner.py earlier.

WHERE IT'S CALLED:
    - server.py - codewalk_get_reading_order() MCP tool
    - pipeline.py - during analysis phase

DEPENDENCIES:
    - config.py: get_llm() for optional LLM relevance tagging
    - dependency_graph.py: provides the file-level dependency graph

=============================================================================
"""

# --- Imports ---

import json
from collections import deque

from src.codewalk.config import get_llm
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser


# =============================================================================
# LLM Prompts for Relevance Tagging
# =============================================================================
# These prompts tell the LLM how to classify each file's reading priority.
# Used by tag_reading_relevance() to add "must-read"/"optional"/"skip" tags.

RELEVANCE_SYSTEM_PROMPT = """You are a code onboarding expert. Given a list of files
from a software project with their dependency information, classify each file's
reading priority for a NEW developer trying to understand the codebase.

For each file, return one of:
- "must-read": Core logic, entry points, orchestrators, key business logic.
  Files a new developer MUST read to understand how the project works.
- "optional": Useful but not essential. Config, utilities, helpers, 
  simple wrappers. Read if you want deeper understanding.
- "skip": Empty markers, boilerplate, generated stubs, files with no 
  meaningful logic. No value for understanding the project.

RULES:
- Entry points (main.py, app.py, server.py, index.ts) -> always must-read
- Pipeline/orchestrator files -> must-read
- Files imported by many others (high fan-out) -> must-read
- __init__.py with no real code -> skip
- Config files with just settings -> optional
- Pure utility/helper files -> optional
- Files that only re-export -> skip
- When in doubt -> optional (never hide files unnecessarily)

Return valid JSON only. No markdown, no explanation."""

RELEVANCE_HUMAN_PROMPT = """Classify reading priority for these {total_files} files.

Each line shows: position, file path, dependency info.

{file_info}

Return a JSON object mapping each file path to "must-read", "optional", or "skip".
Example:
{{
  "pipeline.py": "must-read",
  "config.py": "optional",
  "__init__.py": "skip"
}}"""


# =============================================================================
# topological_sort() - Order Files by Dependencies
# =============================================================================

def topological_sort(graph: dict[str, list[str]]) -> list[str]:
    """Sort files so dependencies come before dependents (Kahn's algorithm).

    ALGORITHM (BFS-based topological sort):
        1. Count in-degree for each file (how many internal files THIS file imports)
           Files with 0 in-degree = they don't depend on any other internal file.
        2. Start with all zero-in-degree files (no internal dependencies)
        3. Remove them from the graph - this might make OTHER files have 0 in-degree
        4. Repeat until all files are placed

    WHY KAHN'S ALGORITHM (not DFS)?
        - Deterministic output (sorted() makes it stable)
        - Easy to detect cycles (remaining files after BFS = cyclic)
        - Intuitive: "peel off" independent files layer by layer

    HANDLING CYCLES:
        If file A imports B and B imports A (circular dependency),
        neither reaches 0 in-degree. They get appended at the end.

    Args:
        graph: {"file_a.py": ["file_b.py", "external_pkg"], ...}
               Values may contain external imports (not in graph keys) - ignored.

    Returns:
        Ordered list of file paths (internal files only, external deps excluded).
    """
    # Only consider internal files (ones that ARE in the graph as keys)
    internal_files = set(graph.keys())

    # in_degree[file] = number of INTERNAL files this file depends on
    in_degree = {file: 0 for file in internal_files}

    # dependents[file] = list of files that depend on this file
    dependents = {file: [] for file in internal_files}

    for file, deps in graph.items():
        for dep in deps:
            if dep in internal_files:  # Only count internal dependencies
                in_degree[file] += 1
                dependents[dep].append(file)

    # Start BFS with files that have NO internal dependencies (in_degree=0)
    queue = deque(sorted(file for file in internal_files if in_degree[file] == 0))
    # sorted() ensures deterministic output (same input = same order)

    result = []

    while queue:
        current = queue.popleft()
        result.append(current)

        # Removing 'current' might free up its dependents
        for dependent in sorted(dependents[current]):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # Files still remaining have circular dependencies - add at end
    remaining = [file for file in internal_files if file not in set(result)]
    result.extend(remaining)

    return result


# =============================================================================
# generate_reading_order() - Full Version (With LLM Tagging)
# =============================================================================

def generate_reading_order(files: list[dict], deps: dict) -> dict:
    """Generate reading order WITH LLM relevance tagging.

    EXECUTION FLOW:
        1. Topological sort of the dependency graph
        2. For each file: explain WHY it's in this position
        3. Call LLM to tag each file as must-read/optional/skip
        4. Return complete annotated order

    Returns:
        {
            "order": [
                {"position": 1, "file": "config.py", "why": "No internal deps", "relevance": "optional"},
                {"position": 2, "file": "pipeline.py", "why": "Depends on: config", "relevance": "must-read"},
            ],
            "total_files": 15,
            "has_cycles": False
        }
    """
    graph = deps["graph"]
    sorted_files = topological_sort(graph)

    # Detect cycles: if toposort produced fewer files than expected
    internal_files = set(graph.keys())
    has_cycles = len(sorted_files) < len(internal_files)

    # Build "used by" lookup: for each file, who imports it?
    used_by = {file: [] for file in internal_files}
    for file, file_deps in graph.items():
        for dep in file_deps:
            if dep in internal_files:
                used_by[dep].append(file.split("/")[-1])

    # Build annotated order list
    order = []
    for index, file_path in enumerate(sorted_files):
        deps_list = [dep for dep in graph.get(file_path, []) if dep in internal_files]
        users = used_by.get(file_path, [])

        # Build "why" explanation
        if not deps_list:
            why = "No internal dependencies"
        else:
            dep_names = [dep.split("/")[-1] for dep in deps_list]
            why = f"Depends on: {', '.join(dep_names)}"
        if users:
            why += f" | Used by: {', '.join(users)}"

        order.append({
            "position": index + 1,
            "file": file_path,
            "why": why,
        })

    # Tag with LLM relevance
    order = tag_reading_relevance(order)

    return {
        "order": order,
        "total_files": len(sorted_files),
        "has_cycles": has_cycles,
    }


# =============================================================================
# generate_reading_order_raw() - Without LLM (For MCP Tools)
# =============================================================================

def generate_reading_order_raw(files: list[dict], deps: dict) -> dict:
    """Generate reading order WITHOUT LLM relevance tagging.

    Used by MCP tools where the host LLM (Copilot) does its own reasoning.
    Same as generate_reading_order() but skips the tag_reading_relevance() call.
    """
    graph = deps["graph"]
    sorted_files = topological_sort(graph)

    internal_files = set(graph.keys())
    has_cycles = len(sorted_files) < len(internal_files)

    used_by = {file: [] for file in internal_files}
    for file, file_deps in graph.items():
        for dep in file_deps:
            if dep in internal_files:
                used_by[dep].append(file.split("/")[-1])

    order = []
    for index, file_path in enumerate(sorted_files):
        deps_list = [dep for dep in graph.get(file_path, []) if dep in internal_files]
        users = used_by.get(file_path, [])

        if not deps_list:
            why = "No internal dependencies"
        else:
            dep_names = [dep.split("/")[-1] for dep in deps_list]
            why = f"Depends on: {', '.join(dep_names)}"
        if users:
            why += f" | Used by: {', '.join(users)}"

        order.append({
            "position": index + 1,
            "file": file_path,
            "why": why,
        })

    return {
        "order": order,
        "total_files": len(sorted_files),
        "has_cycles": has_cycles,
    }


# =============================================================================
# tag_reading_relevance() - LLM-Based File Priority Classification
# =============================================================================

def tag_reading_relevance(order: list[dict]) -> list[dict]:
    """Use LLM to tag each file as must-read / optional / skip.

    EXECUTION FLOW:
        1. Format all files + their dependency info into a prompt
        2. Send to LLM - get JSON response with tags
        3. Parse JSON - merge tags into order list
        4. If LLM fails - fallback: everything is "optional"

    WHY LLM FOR THIS?
        Rules-based approach can't understand file PURPOSE.
        "utils.py" might be critical infrastructure (must-read) or
        a trivial helper (optional) - only reading the dependency info
        and file name gives enough context to decide.
    """
    if not order:
        return order

    # Format file info for the prompt
    lines = []
    for item in order:
        lines.append(f"  {item['position']}. {item['file']} - {item['why']}")
    file_info = "\n".join(lines)

    prompt = ChatPromptTemplate.from_messages([
        ("system", RELEVANCE_SYSTEM_PROMPT),
        ("human", RELEVANCE_HUMAN_PROMPT),
    ])

    llm = get_llm()
    chain = prompt | llm | StrOutputParser()

    result = chain.invoke({
        "total_files": len(order),
        "file_info": file_info,
    })

    # Parse JSON response (strip markdown fences if present)
    text = result.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])

    try:
        tags = json.loads(text)
    except json.JSONDecodeError:
        # LLM returned invalid JSON - safe fallback
        for item in order:
            item["relevance"] = "optional"
        return order

    # Merge LLM tags into order list
    for item in order:
        item["relevance"] = tags.get(item["file"], "optional")

    return order
