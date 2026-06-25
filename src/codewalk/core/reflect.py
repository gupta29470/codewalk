"""Actor-critic reflection loop utilities for self-improving LLM outputs."""
from __future__ import annotations
from typing import TypeVar, Callable
from langchain_core.messages import SystemMessage, HumanMessage

from src.codewalk.config import get_llm

T = TypeVar("T")


def reflect(initial_output: T, context: str, 
    critic_system_prompt: str, 
    improve_fn: Callable[[T, str], T], # takes (current_output, raw_critique_text) → improved output
    iterations: int = 1) -> T:
    """Generic Actor→Critic→Improve loop. Works for any LLM output type.

    Args:
        initial_output:      The first-pass output to improve (ReviewResult, plan dict, test suite...)
        context:             The input that produced the output (diff text, spec, question...)
        critic_system_prompt: Domain-specific instructions for the critic role
        improve_fn:          Domain-specific function that applies critique to produce improved output
        iterations:          Number of reflect→improve cycles (1 is enough for most cases)

    Returns:
        Improved output of the same type as initial_output.
    """
    llm = get_llm(temperature=0)
    output = initial_output

    for _ in range(iterations):
        response = llm.invoke([
            SystemMessage(content=critic_system_prompt),
            HumanMessage(content=_build_critic_input(output, context)),
        ])
        raw_critique = response.content.strip()
        output = improve_fn(output, raw_critique)
    
    return output

def _build_critic_input(output: object, context: str) -> str:
    """Format the critic's user message: context + serialized output."""
    import json
    import dataclasses
    try:
        if dataclasses.is_dataclass(output) and not isinstance(output, type):
            output_str = json.dumps(dataclasses.asdict(output), default=str, indent=2)
        else:
            output_str = json.dumps(output, default=str, indent=2)
    except Exception:
        output_str = str(output)
    return f"CONTEXT:\n```\n{context[:10000]}\n```\n\nOUTPUT TO CRITIQUE:\n{output_str}"

