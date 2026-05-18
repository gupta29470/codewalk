"""
=============================================================================
 overview_generator.py - LLM-Generated Project Overview Document
=============================================================================

WHAT THIS FILE DOES:
    Generates a Markdown overview document for the entire project using an LLM.
    Takes structured data (modules, tech stack, diagram) and produces a
    human-readable onboarding document.

    Example output: "This project contains 5 modules across 20 files.
    The 'analysis' module handles code parsing... The 'embeddings' module..."

HOW IT WORKS:
    1. Receives structured data from detect_modules() and diagram_generator
    2. Formats it into a detailed prompt
    3. Sends to LLM (Claude/GPT)
    4. Returns Markdown overview

WHERE IT'S CALLED:
    - server.py -> codewalk_get_overview() MCP tool (now removed - was LLM call)
    - Can still be called directly for non-MCP usage

DEPENDENCIES:
    - config.py: get_llm() for the language model
    - langchain: prompt templates

=============================================================================
"""

# --- Imports ---

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from src.codewalk.config import settings, get_llm


# =============================================================================
# LLM Prompt
# =============================================================================

OVERVIEW_SYSTEM_PROMPT = """You are a software architect writing onboarding documentation for a new team member.

You will receive structured information about a codebase including:
- Tech stack (languages, frameworks)
- Module breakdown (folders, file counts, languages per module)
- Module dependencies (which module depends on which)
- Architecture diagram (Mermaid syntax)

Write a clear, concise project overview in Markdown format. Include:

1. **Project Structure** (2-3 sentences): Describe what this project appears to contain based on the modules and tech stack listed. Do NOT guess the project's business purpose - only describe what you can see from the structure.
2. **Tech Stack**: List the languages and key technologies.
3. **Architecture Overview**: Describe the high-level structure - what each module likely does based on its name and how they connect via dependencies.
4. **Module Breakdown**: A brief description of each module's likely responsibility based on its name, file count, and dependencies.

RULES:
- Be specific - reference actual module names and file counts.
- Keep it concise - this is a quick-start guide, not a book.
- Use the dependency information to explain data flow.
- Do NOT invent information not provided in the input.
- If a module name is ambiguous, say "likely handles X based on the name" rather than stating it as fact.
- Write in second person ("you", "your") to address the new developer directly.
"""

OVERVIEW_HUMAN_PROMPT = """Here is the codebase analysis:

**Tech Stack**: {tech_stack}

**File Statistics**:
- Total files: {total_files}
- Total modules: {total_modules}

**Modules**:
{module_details}

**Module Dependencies**:
{module_deps}

**Architecture Diagram (Mermaid)**:
```mermaid
{diagram}
```

Please write the project overview document.
"""


# =============================================================================
# Helper Functions
# =============================================================================

def _format_module_details(modules: dict) -> str:
    """Format modules dict into human-readable text for the prompt.

    Input: {"analysis": {"files": [...], "languages": {"python": 3}, "file_count": 3}}
    Output: "- **analysis** (3 files): python(3)\n  Files: code_parser.py, ..."
    """
    parts = []
    for name, info in sorted(modules.items()):
        lang_str = ", ".join(
            f"{lang}({count})" for lang, count in sorted(info["languages"].items())
        )
        file_names = [path.split("/")[-1] for path in info["files"]]
        parts.append(
            f"- **{name}** ({info['file_count']} files): {lang_str}\n"
            f"  Files: {', '.join(sorted(file_names))}"
        )
    return "\n".join(parts)


def _format_module_deps(module_graph: dict) -> str:
    """Format module dependencies into readable arrows.

    Input: {"rag": ["embeddings"], "analysis": ["ingestion"]}
    Output: "- analysis -> ingestion\n- rag -> embeddings"
    """
    lines = []
    for module_name, deps in sorted(module_graph.items()):
        for dep in sorted(deps):
            lines.append(f"- {module_name} -> {dep}")
    return "\n".join(lines) if lines else "No cross-module dependencies detected."


# =============================================================================
# generate_overview() - Main Entry Point
# =============================================================================

def generate_overview(tech_stack: list[str], modules_result: dict, diagram: str) -> str:
    """Generate project overview document using LLM.

    Args:
        tech_stack: ["python", "flutter", "fastapi"]
        modules_result: Full result from detect_modules()
        diagram: Mermaid diagram string from generate_module_diagram()

    Returns:
        Markdown string - the complete project overview document.
    """
    module_details = _format_module_details(modules_result["modules"])
    module_deps = _format_module_deps(modules_result["module_graph"])

    prompt = ChatPromptTemplate.from_messages([
        ("system", OVERVIEW_SYSTEM_PROMPT),
        ("human", OVERVIEW_HUMAN_PROMPT),
    ])

    llm = get_llm()
    chain = prompt | llm | StrOutputParser()

    overview = chain.invoke({
        "tech_stack": ", ".join(tech_stack) if tech_stack else "Not detected",
        "total_files": modules_result["stats"]["total_files"],
        "total_modules": modules_result["stats"]["total_modules"],
        "module_details": module_details,
        "module_deps": module_deps,
        "diagram": diagram,
    })

    return overview
