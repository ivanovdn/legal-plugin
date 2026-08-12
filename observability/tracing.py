# observability/tracing.py
"""Helpers for GENERATION spans + token usage on LLM calls.

Two call styles exist, neither using LangChain's callback integration (that
imports the full ``langchain`` package, not a dependency here). So we instrument
manually:
- Raw httpx POSTs to Ollama (graph nodes) → ``ollama_usage`` maps the response.
- LangChain ``.invoke()`` (skills) → ``traced_invoke`` / ``traced_agent_invoke``
  run the call inside an LLM span and record model + token usage.

Tracing must never break the real call: with no provider the span is a no-op and
``set_gen_attributes`` is a no-op, so every helper returns the underlying result.
"""
from __future__ import annotations

from typing import Any, TypedDict

from observability.spans import set_gen_attributes, traced


class TokenUsage(TypedDict):
    input: int | None
    output: int | None
    total: int | None
    unit: str


def ollama_usage(response_json: dict[str, Any]) -> TokenUsage | None:
    """Map a non-streaming Ollama /api/chat response to token usage."""
    pin = response_json.get("prompt_eval_count")
    pout = response_json.get("eval_count")
    if pin is None and pout is None:
        return None
    return {
        "input": pin,
        "output": pout,
        "total": (pin or 0) + (pout or 0),
        "unit": "TOKENS",
    }


def message_usage(message: Any) -> TokenUsage | None:
    """Token usage from a LangChain AIMessage (usage_metadata, else response_metadata)."""
    um = getattr(message, "usage_metadata", None)
    if isinstance(um, dict):
        pin = um.get("input_tokens")
        pout = um.get("output_tokens")
        total = um.get("total_tokens")
    else:
        rm = getattr(message, "response_metadata", None)
        rm = rm if isinstance(rm, dict) else {}
        pin = rm.get("prompt_eval_count")
        pout = rm.get("eval_count")
        total = None
    if pin is None and pout is None:
        return None
    return {
        "input": pin,
        "output": pout,
        "total": total if total is not None else (pin or 0) + (pout or 0),
        "unit": "TOKENS",
    }


def _message_model(message: Any) -> str | None:
    rm = getattr(message, "response_metadata", None)
    return rm.get("model") if isinstance(rm, dict) else None


@traced("llm", kind="LLM")
def traced_invoke(llm: Any, messages: Any, *, name: str = "llm") -> Any:
    """Invoke a LangChain chat model as an LLM span (model + token usage)."""
    response = llm.invoke(messages)
    set_gen_attributes(
        name=name,
        input=messages,
        output=getattr(response, "content", None) or str(response),
        model=_message_model(response),
        usage=message_usage(response),
    )
    return response


@traced("agent", kind="LLM")
def traced_agent_invoke(agent: Any, payload: Any, *, name: str = "agent") -> Any:
    """Invoke a LangGraph/LangChain agent, summarizing the run as one LLM span."""
    result = agent.invoke(payload)
    messages = result.get("messages", []) if isinstance(result, dict) else []
    final = messages[-1] if messages else None

    tin = tout = 0
    have_usage = False
    for m in messages:
        u = message_usage(m)
        if u:
            have_usage = True
            tin += u["input"] or 0
            tout += u["output"] or 0
    usage: TokenUsage | None = (
        {"input": tin, "output": tout, "total": tin + tout, "unit": "TOKENS"}
        if have_usage
        else None
    )

    set_gen_attributes(
        name=name,
        input=payload,
        output=(getattr(final, "content", None) or str(final)) if final else None,
        model=_message_model(final) if final is not None else None,
        usage=usage,
    )
    return result
