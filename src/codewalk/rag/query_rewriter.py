"""RAG query rewriting for corrective-RAG retries."""
from langchain_core.prompts import ChatPromptTemplate

from src.codewalk.config import get_llm
from src.codewalk.log import log as _log

_REWRITE_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are a query rewriter for a code search engine.\n"
     "The original query failed to retrieve relevant code chunks.\n"
     "Rewrite it to be more specific and likely to match source code.\n"
     "Add likely function names, file names, or technical terms.\n"
     "Return ONLY the rewritten query, nothing else."),
    ("human",
     "Original query: {question}\n"
     "Rewrite it to better match source code:"),
])

def rewrite_query(question: str) -> str:
    """Rewrite a failed query to be more specific for code search.

    Cost: 1 LLM call. Only called when retrieval fails.
    """
    from langchain_core.output_parsers import StrOutputParser

    llm = get_llm(temperature=0.3, reasoning=False)
    chain = _REWRITE_PROMPT | llm | StrOutputParser()

    rewritten = chain.invoke({"question": question}).strip()
    _log(f"[rewriter] '{question[:50]}' → '{rewritten[:50]}'")

    return rewritten

