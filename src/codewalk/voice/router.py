"""Voice request router: transcribed speech → tool selection → answer generation."""
import json
from src.codewalk.config import settings, get_llm
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# ── Tool registry: all 16 Codewalk tools with schemas ──

TOOL_REGISTRY = {
    "codewalk_analyze_codebase": {
        "description": "Analyze repo structure — modules, dependencies. Must call first.",
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
    "codewalk_get_architecture_health": {
        "description": "Architecture health: bottlenecks, key files, circular dependencies, refactoring priorities.",
        "parameters": {},
    },
    "codewalk_call_chain": {
        "description": "Trace the shortest import chain between two files.",
        "parameters": {
            "source": {"type": "string", "description": "Source file name or path"},
            "target": {"type": "string", "description": "Target file name or path"},
        },
    },
    "codewalk_index_docs": {
        "description": "Index a folder of documents (.md, .pdf, .txt) for semantic search.",
        "parameters": {"docs_path": {"type": "string", "description": "Path to documents folder"}},
    },
    "codewalk_search_docs": {
        "description": "Search indexed documents for content matching a query.",
        "parameters": {"query": {"type": "string", "description": "What to search for"}},
    },
    "codewalk_ask_docs": {
        "description": "Ask a question and get an answer grounded in indexed documents.",
        "parameters": {"question": {"type": "string", "description": "The question to answer from docs"}},
    },
    # "codewalk_voice_ask" — not in routing map (voice_ask IS the router entry point)
    # "codewalk_speak" — not routable (TTS output, not a query)
    # "codewalk_approve_action" — not routable via voice (requires explicit text)
    # "codewalk_reflect_review" — not routable via voice (requires initial_review text)
}


# System prompt for the routing LLM
ROUTER_SYSTEM_PROMPT = """You are a tool router for Codewalk, a codebase onboarding tool.
Given the user's spoken request, pick the BEST matching tool and extract arguments.

Available tools:
{tools_description}

RULES:
- Ignore filler words: "um", "uh", "like", "so", "basically", "can you"
- Match partial names: "scanner" → "scanner.py", "blast" → blast_radius_map
- "overview" or "summary" → codewalk_get_overview (not search)
- "what does X do" → codewalk_explain_function (not search)
- "what's in module X" → codewalk_get_module_info (not search)
- "what breaks" / "risk" / "impact" → codewalk_get_blast_radius_map
- "this project", "the project", "whole project", "entire codebase", "everything" → leave optional params EMPTY (do NOT invent a module name)

DEFAULT RULE — when in doubt, use codewalk_search_codebase:
- "how does X work" → ALWAYS search, never execution_flow
- "X flow" where X is a concept/feature (auth, payment, login) → ALWAYS search
- codewalk_get_execution_flow is ONLY for "show me the dependency diagram" or "execution flow of the whole project" — never for feature concepts
- codewalk_get_module_info is ONLY when user names an EXACT top-level module
- If you are not sure which tool → codewalk_search_codebase with the full transcript as query

EXAMPLES:
User: "give me the reading order for analysis"
{{"tool": "codewalk_get_reading_order", "arguments": {{"module_name": "analysis"}}}}

User: "what does scan directory do"
{{"tool": "codewalk_explain_function", "arguments": {{"function_name": "scan_directory"}}}}

User: "um what breaks if I change the scanner"
{{"tool": "codewalk_get_blast_radius_map", "arguments": {{"target": "scanner.py"}}}}

User: "show me the overview"
{{"tool": "codewalk_get_overview", "arguments": {{}}}}

User: "how does authentication work"
{{"tool": "codewalk_search_codebase", "arguments": {{"query": "authentication"}}}}

User: "authentication flow"
{{"tool": "codewalk_search_codebase", "arguments": {{"query": "authentication flow"}}}}

User: "show me the login flow"
{{"tool": "codewalk_search_codebase", "arguments": {{"query": "login flow"}}}}

User: "show me the dependency diagram"
{{"tool": "codewalk_get_execution_flow", "arguments": {{}}}}

User: "what's in the API module"
{{"tool": "codewalk_get_module_info", "arguments": {{"module_name": "api"}}}}

User: "reading order of this project"
{{"tool": "codewalk_get_reading_order", "arguments": {{}}}}

User: "show me the blast radius for the whole project"
{{"tool": "codewalk_get_blast_radius_map", "arguments": {{}}}}

User: "review my changes"
{{"tool": "codewalk_review_diff", "arguments": {{}}}}

User: "review the pipeline file"
{{"tool": "codewalk_review_file", "arguments": {{"file_path": "src/codewalk/pipeline.py"}}}}

User: "reindex changed files"
{{"tool": "codewalk_incremental_reindex", "arguments": {{}}}}

User: "refresh the analysis"
{{"tool": "codewalk_refresh_analysis", "arguments": {{}}}}

User: "load our coding guidelines"
{{"tool": "codewalk_load_guidelines", "arguments": {{}}}}

User: "show me the architecture health"
{{"tool": "codewalk_get_architecture_health", "arguments": {{}}}}

User: "are there any circular dependencies"
{{"tool": "codewalk_get_architecture_health", "arguments": {{}}}}

User: "how does pipeline connect to config"
{{"tool": "codewalk_call_chain", "arguments": {{"source": "pipeline.py", "target": "config.py"}}}}

User: "import chain from scanner to vector store"
{{"tool": "codewalk_call_chain", "arguments": {{"source": "scanner.py", "target": "vector_store.py"}}}}

User: "index our team docs"
{{"tool": "codewalk_index_docs", "arguments": {{"docs_path": "/path/to/docs"}}}}

User: "search docs for deployment process"
{{"tool": "codewalk_search_docs", "arguments": {{"query": "deployment process"}}}}

User: "how do we deploy to production"
{{"tool": "codewalk_ask_docs", "arguments": {{"question": "how do we deploy to production"}}}}

Return ONLY valid JSON, nothing else:
{{"tool": "tool_name", "arguments": {{...}}}}

If the question is NOT about code/project/architecture at all (e.g. "what time is it", "tell me a joke"):
{{"tool": null, "arguments": {{}}}}
"""

def _build_tools_description() -> str:
    """Build a concise tool list for the system prompt."""
    lines = []

    for name, info in TOOL_REGISTRY.items():
        params = ", ".join(f"{k}: {v['type']}" for k, v in info["parameters"].items()) if info["parameters"] else "none"
        lines.append(f"- {name}({params}): {info['description']}")
    return "\n".join(lines)

def route(transcript: str) -> dict:
    """Route a voice transcript to the best Codewalk tool.

    Uses the user's configured LLM (via get_llm()) for routing.

    Returns:
        {"tool": "tool_name", "arguments": {...}} or {"tool": None}
    """
    system_prompt = ROUTER_SYSTEM_PROMPT.format(
        tools_description=_build_tools_description()
    )
    # Escape braces so ChatPromptTemplate treats JSON examples as literal text.
    system_prompt = system_prompt.replace("{", "{{").replace("}", "}}")

    llm = get_llm(temperature=0)
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{transcript}"),
    ])
    chain = prompt | llm | StrOutputParser()
    content = chain.invoke({"transcript": transcript})

    try:
        result = json.loads(content)
        if "tool" not in result or (result.get("tool") and result["tool"] not in TOOL_REGISTRY):
            return {"tool": None, "arguments": {}}
        return result
    except (json.JSONDecodeError, KeyError):
        return {"tool": None, "arguments": {}}