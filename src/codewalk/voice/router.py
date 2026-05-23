"""
=============================================================================
 router.py - Voice Command Router (Transcript -> Tool Selection)
=============================================================================

WHAT THIS FILE DOES:
    Takes a user's spoken transcript and determines which Codewalk tool
    to call and with what arguments.

    Example: "what does scan directory do" -> codewalk_explain_function(function_name="scan_directory")

HOW IT WORKS:
    1. TOOL_REGISTRY defines all 16 tools with parameter schemas
    2. ROUTER_SYSTEM_PROMPT teaches the LLM how to route
    3. route_with_ollama() uses local qwen2.5:1.5b (free, 398MB)
    4. route_with_llm() uses the user's configured LLM (API key)
    5. route() auto-picks based on config

WHERE IT'S CALLED:
    - companion.py -> main loop calls route_with_ollama()
    - api/main.py -> /voice/ask endpoint

DEPENDENCIES:
    - config.py: settings.llm_provider for auto-routing
    - httpx: for Ollama HTTP calls
    - langchain: for LLM routing (API key path)

=============================================================================
"""

import json
import httpx
from src.codewalk.config import settings


# =============================================================================
# Tool Registry - All 16 Codewalk Tools
# =============================================================================
# This is the voice companion's understanding of what tools exist.
# Each entry has a description (for the routing prompt) and parameter schemas.

TOOL_REGISTRY = {
    "codewalk_analyze_codebase": {
        "description": "Analyze repo structure - modules, dependencies. Must call first.",
        "parameters": {},
    },
    "codewalk_search_codebase": {
        "description": "Semantic search the indexed codebase for code snippets.",
        "parameters": {"query": {"type": "string", "description": "Natural language search query"}},
    },
    "codewalk_get_module_info": {
        "description": "Get files, symbols, and dependencies for a specific module.",
        "parameters": {"module_name": {"type": "string", "description": "Module name, e.g. 'analysis'"}},
    },
    "codewalk_explain_function": {
        "description": "Find and explain a function or class by name, line by line.",
        "parameters": {"function_name": {"type": "string", "description": "Function or class name"}},
    },
    "codewalk_get_overview": {
        "description": "High-level overview: tech stack, modules, diagram, riskiest files.",
        "parameters": {},
    },
    "codewalk_get_blast_radius_map": {
        "description": "Show what breaks if you change a file or module.",
        "parameters": {"target": {"type": "string", "description": "Module name, file name, or empty for top 15", "default": ""}},
    },
    "codewalk_get_reading_order": {
        "description": "Recommended file reading order based on dependencies.",
        "parameters": {"module_name": {"type": "string", "description": "Optional module to scope to", "default": ""}},
    },
    "codewalk_get_execution_flow": {
        "description": "Dependency flow diagram. Module-level or file-level.",
        "parameters": {"module_name": {"type": "string", "description": "Optional module for file-level flow", "default": ""}},
    },
    "codewalk_scan_files": {
        "description": "Get batch of file paths for filtering during setup.",
        "parameters": {"batch": {"type": "integer", "description": "Batch number, start at 1", "default": 1}},
    },
    "codewalk_submit_filtered_files": {
        "description": "Submit relevant file paths from current batch to index.",
        "parameters": {"paths": {"type": "array", "description": "List of file paths to index"}},
    },
    "codewalk_index_filtered_files": {
        "description": "Embed all submitted files into the vector store.",
        "parameters": {},
    },
    "codewalk_incremental_reindex": {
        "description": "Re-embed only files that changed since last indexing.",
        "parameters": {},
    },
    "codewalk_refresh_analysis": {
        "description": "Rebuild deps and modules without re-embedding.",
        "parameters": {},
    },
    "codewalk_review_diff": {
        "description": "Review git diff for bugs, security, and style issues.",
        "parameters": {
            "staged": {"type": "boolean", "description": "Review staged changes only", "default": False},
            "target_branch": {"type": "string", "description": "Diff against branch", "default": None},
        },
    },
    "codewalk_review_file": {
        "description": "Review a single file against codebase patterns.",
        "parameters": {"file_path": {"type": "string", "description": "Path to file to review"}},
    },
    "codewalk_load_guidelines": {
        "description": "Load team coding guidelines for use in reviews.",
        "parameters": {"docs_path": {"type": "string", "description": "Path to guidelines directory", "default": None}},
    },
}


# =============================================================================
# Router System Prompt
# =============================================================================

ROUTER_SYSTEM_PROMPT = """You are a tool router for Codewalk, a codebase onboarding tool.
Given the user's spoken request, pick the BEST matching tool and extract arguments.

Available tools:
{tools_description}

RULES:
- Ignore filler words: "um", "uh", "like", "so", "basically", "can you"
- Match partial names: "scanner" -> "scanner.py", "blast" -> blast_radius_map
- "overview" or "summary" -> codewalk_get_overview (not search)
- "what does X do" -> codewalk_explain_function (not search)
- "what's in module X" -> codewalk_get_module_info (not search)
- "what breaks" / "risk" / "impact" -> codewalk_get_blast_radius_map
- "this project", "the project", "whole project" -> leave optional params EMPTY

DEFAULT RULE - when in doubt, use codewalk_search_codebase:
- "how does X work" -> ALWAYS search
- "X flow" where X is a concept/feature -> ALWAYS search
- codewalk_get_execution_flow is ONLY for dependency diagrams
- codewalk_get_module_info is ONLY when user names an EXACT module
- If unsure -> codewalk_search_codebase with full transcript as query

Return ONLY valid JSON:
{{"tool": "tool_name", "arguments": {{...}}}}

If not about code/project at all:
{{"tool": null, "arguments": {{}}}}
"""


def _build_tools_description() -> str:
    """Build concise tool list for the system prompt."""
    lines = []
    for name, info in TOOL_REGISTRY.items():
        params = ", ".join(f"{k}: {v['type']}" for k, v in info["parameters"].items()) if info["parameters"] else "none"
        lines.append(f"- {name}({params}): {info['description']}")
    return "\n".join(lines)


# =============================================================================
# Routing Functions
# =============================================================================

def route_with_ollama(transcript: str, model: str = "qwen2.5:1.5b") -> dict:
    """Route using local Ollama (free, no API key needed).

    Uses a tiny model (398MB) that's fast enough for routing.
    Falls back to {"tool": None} on any error.

    EXAMPLE TRACE (transcript="what does scan directory do"):
        system_prompt  = ROUTER_SYSTEM_PROMPT.format(tools_description=_build_tools_description())
        response       = httpx.post("http://localhost:11434/api/chat", json={...})  → 200
        content        = '{"tool": "codewalk_explain_function", "arguments": {"function_name": "scan_directory"}}'
        result         = json.loads(content)  → {"tool": "codewalk_explain_function", "arguments": {"function_name": "scan_directory"}}
        result["tool"] in TOOL_REGISTRY  → True  ✓
        return → {"tool": "codewalk_explain_function", "arguments": {"function_name": "scan_directory"}}

    EXAMPLE TRACE (transcript="what's the weather today" → non-code):
        content = '{"tool": null, "arguments": {}}'
        return → {"tool": None, "arguments": {}}
    """
    system_prompt = ROUTER_SYSTEM_PROMPT.format(
        tools_description=_build_tools_description()
    )

    response = httpx.post(
        "http://localhost:11434/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": transcript},
            ],
            "stream": False,
            "format": "json",
        },
        timeout=30,
    )
    response.raise_for_status()
    content = response.json()["message"]["content"]

    try:
        result = json.loads(content)
        if result.get("tool") and result["tool"] not in TOOL_REGISTRY:
            return {"tool": None, "arguments": {}}
        return result
    except json.JSONDecodeError:
        return {"tool": None, "arguments": {}}


def route_with_llm(transcript: str) -> dict:
    """Route using the user's configured LLM (for API key users)."""
    from src.codewalk.config import get_llm
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser

    system_prompt = ROUTER_SYSTEM_PROMPT.format(
        tools_description=_build_tools_description()
    )

    llm = get_llm(temperature=0)
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{transcript}"),
    ])
    chain = prompt | llm | StrOutputParser()
    content = chain.invoke({"transcript": transcript})

    try:
        result = json.loads(content)
        if result.get("tool") and result["tool"] not in TOOL_REGISTRY:
            return {"tool": None, "arguments": {}}
        return result
    except (json.JSONDecodeError, KeyError):
        return {"tool": None, "arguments": {}}


def route(transcript: str) -> dict:
    """Auto-pick routing strategy based on config.

    Ollama users -> local qwen2.5:1.5b (free)
    API key users -> their configured LLM
    """
    if settings.llm_provider == "ollama":
        return route_with_ollama(transcript)
    else:
        return route_with_llm(transcript)