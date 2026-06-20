"""Query expansion for improved code retrieval.

Rewrites a natural-language question into multiple retrieval queries and
optionally extracts a likely symbol name, so semantic search has more chances
to match the relevant code.
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

from src.codewalk.config import get_llm
from src.codewalk.log import log as _log


class ExpandedQuery(BaseModel):
    """LLM output for query expansion."""

    original: str = Field(description="The original user question.")
    queries: list[str] = Field(
        description=(
            "2-4 search queries that together cover different angles of the question. "
            "Include the original question, plus rephrasings using technical/code terms."
        )
    )
    symbol_hint: str | None = Field(
        default=None,
        description=(
            "If the question names or strongly implies a specific function/class, "
            "put its name here (e.g., 'scan_directory', 'GraphStore')."
        ),
    )


_EXPAND_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are a query expansion assistant for a codebase search engine.\n"
     "Given a user's question, produce 2-4 retrieval queries that rephrase the\n"
     "question using different technical terms. Also extract any specific\n"
     "function or class name mentioned or strongly implied.\n\n"
     "Examples:\n"
     "- Question: 'How does authentication work?'\n"
     "  queries: ['How does authentication work?', 'auth login flow', 'verify user credentials']\n"
     "- Question: 'What does scan_directory return?'\n"
     "  queries: ['What does scan_directory return?', 'scan_directory function return value']\n"
     "  symbol_hint: 'scan_directory'"),
    ("human", "Question: {question}"),
])


def expand_query(question: str) -> ExpandedQuery:
    """Expand a question into multiple retrieval queries + optional symbol hint.

    Cost: 1 LLM call. Used when initial retrieval is weak.
    """
    llm = get_llm(temperature=0, reasoning=False)
    expander = _EXPAND_PROMPT | llm.with_structured_output(ExpandedQuery)
    result: ExpandedQuery = expander.invoke({"question": question})
    # Ensure the original question is always included
    if result.original != question:
        result.original = question
    if question not in result.queries:
        result.queries.insert(0, question)
    _log(f"[query_expander] {len(result.queries)} queries, symbol_hint={result.symbol_hint}")
    return result
