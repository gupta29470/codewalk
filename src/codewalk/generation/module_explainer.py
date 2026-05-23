"""
=============================================================================
 module_explainer.py - LLM-Generated Module Explanations
=============================================================================

WHAT THIS FILE DOES:
    Generates a detailed explanation of ONE module using an LLM.
    Takes module info (files, dependencies) and produces a Markdown
    explanation suitable for developer onboarding.

WHERE IT'S CALLED:
    - server.py -> was used by codewalk_explain_module() (now removed)
    - Can still be called directly for non-MCP usage

DEPENDENCIES:
    - config.py: get_llm()
    - langchain: prompt templates

=============================================================================
"""

import logging
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from src.codewalk.config import settings, get_llm
from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")

# --- LLM Prompt ---

MODULE_SYSTEM_PROMPT = """You are a senior software engineer explaining a code module to a new team member.

You will receive information about ONE module in a codebase including:
- Module name
- Files in the module
- Languages used
- What other modules this module depends on
- What other modules depend on THIS module

Write a clear, concise module explanation in Markdown format. Include:

1. **Purpose** (1-2 sentences): What this module does and why it exists.
2. **Key Files**: List each file with a one-line description of its likely role.
3. **Dependencies**: What this module needs from other modules, and who needs this module.
4. **Role in the System**: How this module fits in the overall data flow.

RULES:
- Be specific - use actual file names and module names.
- Keep it concise - 1 paragraph per section max.
- Infer file purpose from the filename and module context.
- If a filename is generic (utils, helpers, common, base, misc, types), say "utility/shared code" rather than guessing specific contents.
- Do NOT invent implementation details, class names, or function signatures.
- Write in second person ("you") to address the reader directly.
"""

MODULE_HUMAN_PROMPT = """Here is the module information:

**Module Name**: {module_name}

**Files** ({file_count} total):
{file_list}

**Languages**: {languages}

**This module depends on**: {depends_on}
**Other modules that depend on this module**: {depended_by}

Please write the module explanation.
"""


# --- Helpers ---

def _get_depended_by(module_name: str, module_graph: dict) -> list[str]:
    """Find which modules depend ON this module (reverse lookup)."""
    depended_by = []
    for other_module, dependencies in module_graph.items():
        if module_name in dependencies:
            depended_by.append(other_module)
    return depended_by


def _format_file_list(files: list[str]) -> str:
    """Format file paths into just filenames."""
    return "\n".join(f"- {path.split('/')[-1]}" for path in sorted(files))


# --- Main Function ---

def explain_module(module_name: str, module_info: dict, module_graph: dict) -> str:
    """Generate explanation for ONE module using LLM.

    EXAMPLE TRACE (codewalk src, module="analysis"):
        module_name  = "analysis"
        module_info  = {"files": ["analysis/blast_radius.py", "analysis/code_parser.py", ...], "file_count": 7, "languages": {"python": 7}}
        module_graph  = {"analysis": ["ingestion"], "embeddings": ["analysis"], ...}
        depends_on   = ["ingestion"]
        depended_by  = ["embeddings", "generation", "mcp"]
        languages    = "python(7)"
        file_list    = "- blast_radius.py\n- code_parser.py\n- dependency_graph.py\n..."
        chain.invoke({"module_name": "analysis", "file_count": 7, ...})
        return → "## Purpose\nThe analysis module builds the structural understanding...\n## Key Files\n..."
    """
    depends_on = module_graph.get(module_name, [])
    depended_by = _get_depended_by(module_name, module_graph)

    file_list = _format_file_list(module_info["files"])
    languages = ", ".join(
        f"{lang}({count})" for lang, count in sorted(module_info["languages"].items())
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", MODULE_SYSTEM_PROMPT),
        ("human", MODULE_HUMAN_PROMPT),
    ])

    llm = get_llm()
    chain = prompt | llm | StrOutputParser()

    return chain.invoke({
        "module_name": module_name,
        "file_count": module_info["file_count"],
        "file_list": file_list,
        "languages": languages,
        "depends_on": ", ".join(depends_on) if depends_on else "None (standalone)",
        "depended_by": ", ".join(depended_by) if depended_by else "None",
    })


def explain_all_modules(module_results: dict) -> dict[str, str]:
    """Generate explanations for ALL modules. Returns {name: markdown}."""
    explanations = {}
    for module_name, module_info in sorted(module_results["modules"].items()):
        _log(f"[explainer] Explaining module: {module_name}...")
        explanations[module_name] = explain_module(
            module_name, module_info, module_results["module_graph"]
        )
    return explanations