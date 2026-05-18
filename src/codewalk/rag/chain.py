"""
=============================================================================
 chain.py - RAG Chain (Retrieve -> Augment -> Generate)
=============================================================================

WHAT THIS FILE DOES:
    Implements the RAG (Retrieval-Augmented Generation) pattern:
      1. User asks a question about the codebase
      2. Search ChromaDB for relevant code chunks (retrieval)
      3. Format chunks into context for the LLM (augmentation)
      4. Send question + context to LLM -> get answer (generation)

HOW IT DIFFERS FROM MCP TOOLS:
    MCP tools: return raw data, let Copilot interpret it
    RAG chain: uses codewalk's OWN LLM to answer (standalone mode)

WHERE IT'S CALLED:
    - api/main.py -> the /ask endpoint uses this for standalone RAG
    - Not used by MCP tools (they return raw search results)

DEPENDENCIES:
    - vector_store.py: ChromaDB search
    - prompts.py: system/human prompts
    - config.py: get_llm()

=============================================================================
"""

import logging
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from src.codewalk.config import settings, get_llm
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.rag.prompts import SYSTEM_PROMPT, QUESTION_PROMPT
from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")


def format_context(results: list[dict]) -> str:
    """Turn search results into a rich context string for the LLM prompt.

    Each result gets a header showing file path, symbol name, and line numbers,
    followed by the actual code text. This gives the LLM enough context to
    reference specific files and functions in its answer.
    """
    parts = []
    for result in results:
        meta = result["metadata"]
        file_path = meta["file_path"]
        symbol_name = meta.get("symbol_name", "")
        symbol_type = meta.get("symbol_type", "")
        start_line = meta.get("start_line", 0)
        end_line = meta.get("end_line", 0)

        header = f"--- {file_path}"
        if symbol_name:
            header += f" | {symbol_type}: {symbol_name}"
        if start_line:
            header += f" (lines {start_line}-{end_line})"
        header += " ---"

        parts.append(f"{header}\n{result['text']}")

    return "\n\n".join(parts)


def ask(question: str, store: VectorStore, n_results: int = 5) -> str:
    """Full RAG pipeline: question -> retrieve -> prompt -> LLM -> answer.

    EXECUTION FLOW:
        1. Search ChromaDB with the question (semantic search)
        2. Format top N results into context string
        3. Build prompt: system instructions + context + question
        4. Send to LLM -> get natural language answer
        5. Return the answer string

    Args:
        question: Natural language question about the codebase
        store: ChromaDB VectorStore instance (must be initialized)
        n_results: How many code chunks to retrieve (default 5)

    Returns:
        LLM's answer as a string
    """
    _log(f"[rag] Question: {question[:80]}...")

    # 1. Retrieve relevant chunks from ChromaDB
    results = store.search(question, n_results=n_results)

    # 2. Format chunks into context string
    context = format_context(results)

    # 3. Build the prompt
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", QUESTION_PROMPT),
    ])

    # 4. Create and run the chain
    llm = get_llm()
    chain = prompt | llm | StrOutputParser()

    answer = chain.invoke({
        "context": context,
        "question": question,
    })

    return answer