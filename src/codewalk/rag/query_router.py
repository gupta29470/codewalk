"""Query router — classifies user queries and routes to the best strategy.

Routes:
  "direct"   → exact symbol lookup (explain_function)
  "search"   → semantic search (search_codebase)
  "module"   → module info lookup (get_module_info)
  "overview" → project overview (get_overview)

Cost: 1 LLM call per query.
"""

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

from src.codewalk.config import get_llm
from src.codewalk.log import log as _log


class QueryRoute(BaseModel):
    """LLM's classification of the query type."""
    route: str = Field(
        description=(
            "One of: 'direct' (specific function/class by name), "
            "'search' (semantic code search), "
            "'module' (module structure/info), "
            "'docs' (guidelines/process/config/documentation question), "
            "'overview' (project-level question)"
        )
    )
    target: str = Field(
        description=(
            "For 'direct': the function/class name. "
            "For 'module': the module name. "
            "For 'docs': the refined docs question. "
            "For 'search'/'overview': the refined search query."
        )
    )


_ROUTER_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are a query router for a code exploration tool.\n"
     "Classify the user's question into one of these categories:\n\n"
     "- 'direct': User asks about a SPECIFIC function, class, or method BY NAME.\n"
     "  Examples: 'What does scan_directory do?', 'Show me the GraphStore class', "
     "  'Explain parse_file'\n"
     "  target = the function/class name (e.g. 'scan_directory')\n\n"
     "- 'search': User asks about a concept, feature, or pattern in the code.\n"
     "  Examples: 'How does authentication work?', 'Where is error handling done?', "
     "  'How are embeddings generated?'\n"
     "  target = a refined search query for semantic search\n\n"
     "- 'module': User asks about a module's structure, files, or dependencies.\n"
     "  Examples: 'What's in the analysis module?', 'Show me the rag module', "
     "  'List files in src/codewalk/review'\n"
     "  target = the module name (e.g. 'analysis')\n\n"
     "- 'docs': User asks about conventions, guidelines, commit rules, config, environment,\n"
     "  process, or documentation.\n"
     "  Examples: 'What is the commit message convention?', 'How are env files handled?', "
     "  'What do the review guidelines say about imports?', 'Explain the barrel import pattern'\n"
     "  target = the docs question itself\n\n"
     "- 'overview': User asks about the project as a whole.\n"
     "  Examples: 'What is this project?', 'Give me an overview', 'What tech stack?', "
     "  'Summarize the architecture'\n"
     "  target = the query itself\n\n"
     "Return the route and target. Pick the most specific route that fits."),
    ("human", "Question: {question}"),
])


def route_query(question: str) -> QueryRoute:
    """Classify a user question and determine the best retrieval strategy.

    Cost: 1 LLM call.

    Returns:
        QueryRoute with .route ('direct'|'search'|'module'|'docs'|'overview')
        and .target (refined query or symbol/module name).
    """
    llm = get_llm(temperature=0, reasoning=False)
    router = _ROUTER_PROMPT | llm.with_structured_output(QueryRoute)

    result: QueryRoute = router.invoke({"question": question})
    _log(f"[router] '{question[:50]}' → route={result.route} target='{result.target[:50]}'")
    return result
