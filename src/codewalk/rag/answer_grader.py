from pydantic import BaseModel, Field, model_validator
from langchain_core.prompts import ChatPromptTemplate

from src.codewalk.config import get_llm
from src.codewalk.log import log as _log


class AnswerGrade(BaseModel):
    """LLM's verdict on the quality of a generated answer.

    Backward-compatible: accepts either boolean `faithful`/`relevant` or the
    newer 0-10 `faithful_score`/`relevant_score`. Boolean inputs are converted
    to 10 (True) or 0 (False).
    """
    faithful_score: int = Field(
        ge=0,
        le=10,
        default=0,
        description=(
            "How well the answer is grounded in the provided context. "
            "10 = fully supported by context; 0 = invents facts not in context."
        ),
    )
    relevant_score: int = Field(
        ge=0,
        le=10,
        default=0,
        description=(
            "How well the answer addresses the user's question. "
            "10 = directly answers the question; 0 = off-topic or unresponsive."
        ),
    )
    reason: str = Field(default="", description="One-line explanation of what's wrong, or 'good' if both scores are high")

    @property
    def faithful(self) -> bool:
        return self.faithful_score >= 7

    @property
    def relevant(self) -> bool:
        return self.relevant_score >= 7

    @model_validator(mode="before")
    @classmethod
    def _convert_boolean_fields(cls, values):
        """Allow legacy boolean fields while preferring score fields."""
        if not isinstance(values, dict):
            return values
        if "faithful" in values and "faithful_score" not in values:
            values["faithful_score"] = 10 if values.pop("faithful") else 0
        if "relevant" in values and "relevant_score" not in values:
            values["relevant_score"] = 10 if values.pop("relevant") else 0
        return values


_ANSWER_GRADER_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "You are a quality grader for a code Q&A system.\n"
     "Given a question, the retrieved code context, and the generated answer, "
     "evaluate the answer on two criteria using a 0-10 scale:\n\n"
     "1. FAITHFUL (0-10): Is the answer grounded in the provided context?\n"
     "   10 = every claim is supported by the context\n"
     "   0 = mentions code, functions, or behavior not in the context\n\n"
     "2. RELEVANT (0-10): Does the answer actually address the user's question?\n"
     "   10 = directly and completely answers the question\n"
     "   0 = correct but off-topic or unresponsive\n\n"
     "Examples:\n"
     "- Question: 'What does scan_directory return?' → answer: 'It returns a list of file paths.' "
     "  (faithful=10, relevant=10 if context confirms)\n"
     "- Question: 'What does scan_directory return?' → answer: 'It logs warnings.' "
     "  (faithful=maybe, relevant=2 — doesn't answer what is returned)"),
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

    _log(f"[answer_grader] faithful={grade.faithful_score} relevant={grade.relevant_score} | {grade.reason[:60]}")
    return grade
