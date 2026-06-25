"""Research question planner: decompose a complex question into parallel sub-questions."""
from __future__ import annotations
from dataclasses import dataclass
from src.codewalk.config import get_llm

DEPTH_CONFIG = {
    "quick":    2,
    "standard": 3,
    "deep":     5,
}

PLANNER_PROMPT = """You are a research planner for a codebase intelligence system.
Break the user's question into {n} specific, independent sub-questions that together
fully answer the original question. Each sub-question must be answerable by searching
the codebase independently.

Return ONLY a numbered list, one sub-question per line. No preamble.

Example for "How does auth work?":
1. Where is authentication logic defined and what files implement it?
2. How are JWT tokens generated, validated, and expired?
3. Which endpoints require authentication and how is it enforced?"""

@dataclass
class SubQuestion:
    """One parallel sub-question in a deep-research plan."""
    id: str        # "sq_1", "sq_2", ...
    text: str      # the sub-question text

def decompose(question: str, depth: str = "standard") -> list[SubQuestion]:
    """Break a complex question into independent sub-questions.

    Args:
        question: The user's complex research question.
        depth:    "quick" (2), "standard" (3), or "deep" (5) sub-questions.

    Returns:
        List of SubQuestion dataclasses, one per planned search.
    """
    n = DEPTH_CONFIG.get(depth, 3)
    llm = get_llm(temperature=0)
    prompt = PLANNER_PROMPT.format(n=n) + f"\n\nQuestion: {question}"
    response = llm.invoke(prompt)

    sub_questions = []

    for index, line in enumerate(response.content.strip().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        # Strip leading "1. " numbering
        text = line.lstrip("0123456789. ").strip()
        if text:
            sub_questions.append(SubQuestion(id=f"sq_{index}", text=text))

    return sub_questions[:n]
        
        
