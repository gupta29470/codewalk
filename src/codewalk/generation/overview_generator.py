from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from src.codewalk.config import settings, get_llm

# ─── THE PROMPT ──────────────────────────────────────────────

OVERVIEW_SYSTEM_PROMPT = """You are a software architect writing onboarding documentation for a new team member.

You will receive structured information about a codebase including:
- Tech stack (languages, frameworks)
- Module breakdown (folders, file counts, languages per module)
- Module dependencies (which module depends on which)
- Architecture diagram (Mermaid syntax)

Write a clear, concise project overview in Markdown format. Include:

1. **Project Summary** (2-3 sentences): What this project does and its core purpose.
2. **Tech Stack**: List the languages and key technologies.
3. **Architecture Overview**: Describe the high-level structure — what each module does and how they connect.
4. **Module Breakdown**: A brief description of each module's responsibility.
5. **Key Entry Points**: Where a new developer should start reading code.

RULES:
- Be specific — reference actual module names and file counts.
- Keep it concise — this is a quick-start guide, not a book.
- Use the dependency information to explain data flow.
- Do NOT invent information not provided in the input.
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

def _format_module_details(modules: dict) -> str:
    """Format the modules dict into a human-readable string for the prompt.

    Args:
        modules: The "modules" dict from detect_modules() result.
                 {"analysis": {"files": [...], "languages": {"python": 3}, "file_count": 3}}

    Returns:
        Formatted string like:
          - **analysis** (3 files): python(3)
            Files: code_parser.py, dependency_graph.py, module_detector.py
    """
    parts = []

    for name, info in sorted(modules.items()):
        # Format languages: {"python": 3, "dart": 1} → "python(3), dart(1)"
        lang_str = ", ".join(
            f"{lang}({count})" for lang, count in sorted(info["languages"].items())
        )

        # Get just filenames (not full paths)
        file_names = [path.split("/")[-1] for path in info["files"]]

        parts.append(
            f"- **{name}** ({info['file_count']} files): {lang_str}\n"
            f"  Files: {', '.join(sorted(file_names))}"
        )
    
    return "\n".join(parts)

def _format_module_deps(module_graph: dict) -> str:
    """Format module dependencies into readable text.

    Args:
        module_graph: {"rag": ["embeddings"], "analysis": ["ingestion"], ...}

    Returns:
        "- rag → embeddings\n- analysis → ingestion"
        Or "No cross-module dependencies detected." if none.
    """
    lines = []
    for module_name, deps in sorted(module_graph.items()):
        for dep in sorted(deps):
            lines.append(f"- {module_name} → {dep}")
    
    return "\n".join(lines) if lines else "No cross-module dependencies detected."

def generate_overview(
    tech_stack: list[str],
    modules_result: dict,
    diagram: str,
) -> str:
    """Generate a project overview using the LLM.

    Args:
        tech_stack: List of detected technologies, e.g. ["python", "flutter"].
        modules_result: Full result dict from detect_modules().
        diagram: Mermaid diagram string from generate_module_diagram().

    Returns:
        Markdown string — the project overview document.
    """
    # Step 1: Format the structured data into prompt-friendly text
    module_details = _format_module_details(modules_result["modules"])
    module_deps = _format_module_deps(modules_result["module_graph"])

    # Step 2: Build the prompt
    prompt = ChatPromptTemplate.from_messages([
        ("system", OVERVIEW_SYSTEM_PROMPT),
        ("human", OVERVIEW_HUMAN_PROMPT),
    ])

    # Step 3: Create the chain (same pattern as chain.py)
    llm = get_llm()
    chain = prompt | llm | StrOutputParser()

    # Step 4: Run it — fill all placeholders and send to LLM
    overview = chain.invoke({
        "tech_stack": ", ".join(tech_stack) if tech_stack else "Not detected",
        "total_files": modules_result["stats"]["total_files"],
        "total_modules": modules_result["stats"]["total_modules"],
        "module_details": module_details,
        "module_deps": module_deps,
        "diagram": diagram,
    })

    return overview