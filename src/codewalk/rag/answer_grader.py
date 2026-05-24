from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

from src.codewalk.config import get_llm
from src.codewalk.log import log as _log

class AnswerGrade(BaseModel):
    """LLM's verdict on the quality of a generated answer."""
    faithful: bool = Field(description="True if answer is grounded in the provided context")
    relevant: bool = Field(description="True if the answer addresses the user's question")
    reason: str = Field(description="One-line explanation of what's wrong, or 'good' if both pass")

_ANSWER_GRADER_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are a quality grader for a code Q&A system.\n"
     "Given a question, the retrieved code context, and the generated answer, "
     "evaluate the answer on two criteria:\n"
     "1. FAITHFUL: Is the answer grounded in the provided context? "
     "(If the answer mentions code, functions, or behavior not in the context, it's unfaithful.)\n"
     "2. RELEVANT: Does the answer actually address the user's question? "
     "(A correct but off-topic answer is irrelevant.)"),
    ("human",
     "Question: {question}\n\n"
     "Context (retrieved code):\n{context}\n\n"
     "Generated answer:\n{answer}\n\n"
     "Grade the answer."),
])

def grade_answer(question: str, context: str, answer: str) -> AnswerGrade:
    """Grade a generated answer for faithfulness and relevance.

    Cost: 1 LLM call. Use this as the opt-in quality check.
    """
    llm = get_llm(temperature=0, reasoning=False)
    grader = _ANSWER_GRADER_PROMPT | llm.with_structured_output(AnswerGrade)

    grade: AnswerGrade = grader.invoke({
        "question": question,
        "context": context,
        "answer": answer,
    })

    _log(f"[answer_grader] faithful={grade.faithful} relevant={grade.relevant} | {grade.reason[:60]}")
    return grade
