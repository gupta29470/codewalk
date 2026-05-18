import logging
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from src.codewalk.config import settings, get_llm
from src.codewalk.embeddings.vector_store import VectorStore
from src.codewalk.rag.prompts import SYSTEM_PROMPT, QUESTION_PROMPT
from src.codewalk.log import log as _log

logger = logging.getLogger("codewalk")

def format_context(results: list[dict]) -> str:
    """Turn search results into a rich context string for the prompt.

    Uses metadata to show file path, function/class name, and line numbers
    so the LLM can reference them in its answer.
    """
    parts = []

    for result in results:
        meta = result["metadata"]
        file_path = meta["file_path"]
        symbol_name = meta.get("symbol_name", "")
        symbol_type = meta.get("symbol_type", "")
        start_line = meta.get("start_line", 0)
        end_line = meta.get("end_line", 0)

        # Build a descriptive header
        header = f"--- {file_path}"
        if symbol_name:
            header += f" | {symbol_type}: {symbol_name}"
        if start_line:
            header += f" (lines {start_line}-{end_line})"
        header += " ---"

        parts.append(f"{header}\n{result['text']}")

    return "\n\n".join(parts)

def ask(question: str, store: VectorStore, n_results: int = 5) -> str:
    _log(f"[rag] Question: {question[:80]}...")
    """Full RAG pipeline: question → retrieve → prompt → LLM → answer."""
    # 1. Retrieve relevant chunks from ChromaDB
    results = store.search(question, n_results=n_results)

    # 2. Format chunks into context string
    context = format_context(results)

    # 3. Build the prompt
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            ("human", QUESTION_PROMPT)
        ]
    )

    # 4. Create the chain: prompt → LLM → parse output as string
    llm = get_llm()
    chain = prompt | llm | StrOutputParser()

    # 5. Run it
    answer = chain.invoke({
        "context": context,
        "question": question,
    })

    return answer