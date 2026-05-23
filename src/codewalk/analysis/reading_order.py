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
    4. Strip <think>...</think> tags from DeepSeek responses before JSON parsing
       (DeepSeek reasoning models emit thinking blocks that break JSON extraction)

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

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from src.codewalk.config import get_llm
from src.codewalk.graph.graph_runtime import GraphRuntime


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

def topological_sort(graph) -> list[str]:
    """Sort files so dependencies come before dependents.

    graph: GraphRuntime (igraph, C-speed) or dict (legacy Kahn's).

    EXAMPLE TRACE (dict path, codewalk's src):
        graph = {
            "config.py": [],
            "log.py": ["config.py"],
            "scanner.py": ["config.py", "log.py"],
            "pipeline.py": ["scanner.py", "config.py", "log.py"],
        }
        internal_files = {"config.py", "log.py", "scanner.py", "pipeline.py"}

        in_degree = {"config.py": 0, "log.py": 1, "scanner.py": 2, "pipeline.py": 3}
        # config.py has 0 internal deps → starts in queue

        queue = deque(["config.py"])  # only config.py has in_degree=0

        Iteration 1: current = "config.py"
          result = ["config.py"]
          dependents["config.py"] = ["log.py", "scanner.py", "pipeline.py"]
          in_degree["log.py"] -= 1 → 0 → add to queue
          in_degree["scanner.py"] -= 1 → 1
          in_degree["pipeline.py"] -= 1 → 2

        Iteration 2: current = "log.py"
          result = ["config.py", "log.py"]
          in_degree["scanner.py"] -= 1 → 0 → add to queue
          in_degree["pipeline.py"] -= 1 → 1

        Iteration 3: current = "scanner.py"
          result = ["config.py", "log.py", "scanner.py"]
          in_degree["pipeline.py"] -= 1 → 0 → add to queue

        Iteration 4: current = "pipeline.py"
          result = ["config.py", "log.py", "scanner.py", "pipeline.py"]

        returns ["config.py", "log.py", "scanner.py", "pipeline.py"]
    """

    if isinstance(graph, GraphRuntime):
        return graph.topological_sort()

    # Step 1: Filter to only internal files (keys of the graph)
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

def generate_reading_order(files: list[dict], deps: dict, graph_runtime=None) -> dict:
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

    EXAMPLE TRACE (codewalk's src, 4 files for brevity):
        sorted_files = ["config.py", "log.py", "scanner.py", "pipeline.py"]
        has_cycles = False  (4 sorted == 4 total)

        used_by = {
            "config.py": ["log.py", "scanner.py", "pipeline.py"],
            "log.py": ["scanner.py", "pipeline.py"],
            "scanner.py": ["pipeline.py"],
            "pipeline.py": []
        }

        order[0] = {"position": 1, "file": "config.py",
                    "why": "No internal dependencies | Used by: log.py, scanner.py, pipeline.py"}
        order[1] = {"position": 2, "file": "log.py",
                    "why": "Depends on: config.py | Used by: scanner.py, pipeline.py"}
        order[2] = {"position": 3, "file": "scanner.py",
                    "why": "Depends on: config.py, log.py | Used by: pipeline.py"}
        order[3] = {"position": 4, "file": "pipeline.py",
                    "why": "Depends on: scanner.py, config.py, log.py"}

        → tag_reading_relevance(order) adds "relevance" to each entry
    """
    graph = deps["graph"]
    sorted_files = topological_sort(graph_runtime or graph)

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

def generate_reading_order_raw(files: list[dict], deps: dict, graph_runtime=None) -> dict:
    """Generate reading order WITHOUT LLM relevance tagging.

    Used by MCP tools where the host LLM (Copilot) does its own reasoning.
    Same as generate_reading_order() but skips the tag_reading_relevance() call.
    """
    graph = deps["graph"]
    sorted_files = topological_sort(graph_runtime or graph)

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
    # Strip <think>...</think> tags (DeepSeek reasoning models)
    import re
    text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
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
