import json

from collections import deque

from src.codewalk.config import get_llm
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

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
- Entry points (main.py, app.py, server.py, index.ts) → always must-read
- Pipeline/orchestrator files → must-read
- Files imported by many others (high fan-out) → must-read
- __init__.py with no real code → skip
- Config files with just settings → optional
- Pure utility/helper files → optional
- Files that only re-export → skip
- When in doubt → optional (never hide files unnecessarily)

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



def topological_sort(graph: dict[str, list[str]]) -> list[str]:
    """Sort files so dependencies come before dependents.

    Args:
        graph: file-level dependency graph from build_dependency_graph()
               {"path/a.py": ["path/b.py", "os"], "path/b.py": []}

    Returns:
        List of file paths in reading order (dependencies first).
        Files with no dependencies come first.
        External imports (not in graph) are ignored.
    """
    # Step 1: Filter to only internal files (keys of the graph)
    internal_files = set(graph.keys())

    # Step 2: Build in-degree count (how many internal deps each file has)
    in_degree = {file: 0 for file in internal_files}

    # Also build adjacency list (reverse: "who depends on me?")
    dependents = {file: [] for file in internal_files}

    for file, deps in graph.items():
        for dep in deps:
            if dep in internal_files:
                in_degree[file] += 1
                dependents[dep].append(file)

    # Step 3: Start with files that have zero in-degree (no internal deps)
    queue = deque(sorted(file for file in internal_files if in_degree[file] == 0))
    # sorted() for deterministic output — same input = same order every time

    result = []

    # Step 4: BFS — peel off zero-dependency files one at a time
    while queue:
        current = queue.popleft()
        result.append(current)

        # For everything that depends on current: reduce their in-degree
        for dependent in sorted(dependents[current]):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # Step 5: Any remaining files have circular deps — add them at the end
    remaining = [file for file in internal_files if file not in set(result)]
    result.extend(remaining)

    return result

def generate_reading_order(files: list[dict], deps: dict) -> dict:
    """Generate a complete reading order from scanned files and dependency graph.

    Args:
        files: from scan_directory() — list of file dicts
        deps: from build_dependency_graph() — {"graph": {...}, "stats": {...}}

    Returns:
        {
            "order": [                # sorted file list
                {"position": 1, "file": "config.py", "why": "No internal dependencies"},
                {"position": 2, "file": "file_filter.py", "why": "Used by: scanner.py"},
                ...
            ],
            "total_files": 15,
            "has_cycles": False       # True if circular deps detected
        }
    """
    graph = deps["graph"]
    sorted_files = topological_sort(graph)

    # Internal file set for cycle detection
    internal_files = set(graph.keys())
    has_cycles = len(sorted_files) < len(internal_files)

    # Build "used by" lookup — who imports this file?
    used_by = {file: [] for file in internal_files}
    for file, file_deps in graph.items():
        for dep in file_deps:
            if dep in internal_files:
                used_by[dep].append(file.split("/")[-1])

    # Build the order list with reasons
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

    order = tag_reading_relevance(order)
    
    return {
        "order": order,
        "total_files": len(sorted_files),
        "has_cycles": has_cycles,
    }

def generate_reading_order_raw(files: list[dict], deps: dict) -> dict:
    """Generate reading order WITHOUT LLM relevance tagging.

    Used by MCP tools where the host LLM (Copilot) does the reasoning.
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

def tag_reading_relevance(order: list[dict]) -> list[dict]:
    """Use LLM to tag each file as must-read / optional / skip.

    Args:
        order: The reading order list from generate_reading_order()
               [{"position": 1, "file": "config.py", "why": "..."}, ...]

    Returns:
        Same list with "relevance" field added to each item.
    """
    if not order:
        return order
    
    # Format file info for the prompt
    lines = []
    for item in order:
        lines.append(f"  {item['position']}. {item['file']} — {item['why']}")
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

    # Parse JSON response
    text = result.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])

    try:
        tags = json.loads(text)
    except json.JSONDecodeError:
        # Fallback: tag everything as "optional" if LLM fails
        for item in order:
            item["relevance"] = "optional"
        return order

    # Merge tags into order
    for item in order:
        item["relevance"] = tags.get(item["file"], "optional")

    return order