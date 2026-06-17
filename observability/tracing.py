# observability/tracing.py
"""Helpers for GENERATION-type spans and token usage on LLM calls.

Two call styles exist in this codebase, and neither uses Langfuse's LangChain
callback integration (that integration imports the full ``langchain`` package,
which is not a dependency here — only ``langchain-core`` / ``langchain-ollama``).
So we instrument manually:

- Raw httpx POSTs to Ollama (graph nodes) → ``ollama_usage`` maps the response.
- LangChain ``.invoke()`` calls (skills) → ``traced_invoke`` / ``traced_agent_invoke``
  run the call inside an ``@observe(as_type="generation")`` span and record
  model + token usage read from the returned message(s)' ``usage_metadata``.

Tracing must never break the real call: with Langfuse disabled the ``@observe``
decorator is a transparent pass-through and ``update_current_observation`` is a
no-op, so every helper returns exactly what the underlying call returns.
"""
from __future__ import annotations

from typing import Any

from langfuse.decorators import langfuse_context, observe
from langfuse.model import ModelUsage


def ollama_usage(response_json: dict[str, Any]) -> ModelUsage | None:
    """Map a non-streaming Ollama /api/chat response to Langfuse token usage.

    Ollama returns ``prompt_eval_count`` (input tokens) and ``eval_count``
    (output tokens) at the top level. Returns None when neither is present
    (e.g. an error response) so callers can pass the result straight through.
    """
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


def message_usage(message: Any) -> ModelUsage | None:
    """Token usage from a LangChain AIMessage.

    Prefers ``usage_metadata`` (``langchain-ollama`` fills it from Ollama's
    eval counts), falling back to the raw ``response_metadata`` counts. Returns
    None when neither carries usable integers.
    """
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


@observe(as_type="generation", capture_input=False, capture_output=False)
def traced_invoke(llm: Any, messages: Any, *, name: str = "llm") -> Any:
    """Invoke a LangChain chat model and record it as a Langfuse GENERATION
    (model + token usage from the returned message). Returns the model response.
    """
    response = llm.invoke(messages)
    langfuse_context.update_current_observation(
        name=name,
        input=messages,
        output=getattr(response, "content", None) or str(response),
        model=_message_model(response),
        usage=message_usage(response),
    )
    return response


@observe(as_type="generation", capture_input=False, capture_output=False)
def traced_agent_invoke(agent: Any, payload: Any, *, name: str = "agent") -> Any:
    """Invoke a LangGraph/LangChain agent and record a GENERATION summarizing the
    run: the final message as output, token usage summed across AI messages.
    Returns the agent result unchanged.
    """
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
    usage: ModelUsage | None = (
        {"input": tin, "output": tout, "total": tin + tout, "unit": "TOKENS"}
        if have_usage
        else None
    )

    langfuse_context.update_current_observation(
        name=name,
        input=payload,
        output=(getattr(final, "content", None) or str(final)) if final else None,
        model=_message_model(final) if final is not None else None,
        usage=usage,
    )
    return result
