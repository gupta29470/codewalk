"""Module Explainer utilities for Codewalk."""
import logging
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from src.codewalk.config import settings, get_llm
from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")

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
- Be specific — use actual file names and module names.
- Keep it concise — 1 paragraph per section max.
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

def _get_depended_by(module_name: str, module_graph: dict) -> list[str]:
    """Find which modules depend ON this module (reverse lookup).

    module_graph maps: module → [modules it depends on]
    We need the reverse: who depends on ME?

    Args:
        module_name: The module to look up, e.g. "embeddings"
        module_graph: {"rag": ["embeddings"], "analysis": ["ingestion"], ...}

    Returns:
        List of module names that depend on module_name.
    """
    depended_by = []
    for other_module, dependencies in module_graph.items():
        if module_name in dependencies:
            depended_by.append(other_module)

    return depended_by

def _format_file_list(files: list[str]) -> str:
    """Format file paths into a readable list with just filenames.

    Args:
        files: ["src/codewalk/analysis/code_parser.py", "src/codewalk/analysis/dependency_graph.py"]

    Returns:
        "- code_parser.py\n- dependency_graph.py"
    """
    return "\n".join(f"- {path.split('/')[-1]}" for path in sorted(files))

def explain_module(
    module_name: str,
    module_info: dict,
    module_graph: dict,
) -> str:
    """Generate an explanation for ONE module using the LLM.

    Args:
        module_name: e.g. "analysis"
        module_info: {"files": [...], "languages": {"python": 3}, "file_count": 3}
        module_graph: Full module dependency graph from detect_modules().

    Returns:
        Markdown string explaining this module.
    """
    # What does this module depend on?
    depends_on = module_graph.get(module_name, [])

    # What depends on THIS module? (reverse lookup)
    depended_by = _get_depended_by(module_name, module_graph)

    # Format data
    file_list = _format_file_list(module_info["files"])
    languages = ", ".join(
        f"{lang}({count})" for lang, count in sorted(module_info["languages"].items())
    )

    # Build and run the chain
    prompt = ChatPromptTemplate.from_messages([
        ("system", MODULE_SYSTEM_PROMPT),
        ("human", MODULE_HUMAN_PROMPT),
    ])

    llm = get_llm()
    chain = prompt | llm | StrOutputParser()

    explanation = chain.invoke({
        "module_name": module_name,
        "file_count": module_info["file_count"],
        "file_list": file_list,
        "languages": languages,
        "depends_on": ", ".join(depends_on) if depends_on else "None (standalone)",
        "depended_by": ", ".join(depended_by) if depended_by else "None",
    })

    return explanation

def explain_all_modules(module_results: dict) -> dict[str, str]:
    """Generate explanations for ALL modules.

    Args:
        modules_result: Full result dict from detect_modules().

    Returns:
        Dict mapping module_name → explanation markdown string.
        {"analysis": "## analysis\n**Purpose**: ...", "rag": "## rag\n..."}
    """
    explanations = {}
    for module_name, module_info in sorted(module_results["modules"].items()):
        _log(f"[explainer] Explaining module: {module_name}...")
        explanations[module_name] = explain_module(
            module_name,
            module_info,
            module_results["module_graph"],
        )
    
    return explanations