# graph/nodes/llm_caller.py
"""LLM caller — sends prompt + retrieved context to Ollama."""

import logging
import time

import httpx

from config import get_settings
from graph.state import LegalAgentState
from observability.degradations import CONTEXT_TRUNCATED, LLM_CALL_FAILED
from observability.spans import mark_failed, record_degradation, set_gen_attributes, traced
from observability.tracing import ollama_timings, ollama_usage

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = """You are a legal assistant for an internal legal team. Answer the user's request using ONLY the provided context. For every claim, cite the source document (doc_title and doc_id). If the context is insufficient, say so explicitly — do not fabricate information."""

# Task types whose prompt must NOT carry conversational history.
#
# A document review has to be deterministic in its inputs: the same document
# must yield the same findings regardless of what was said in the chat tab.
# Observed otherwise — an unrelated doc-chat turn suppressed a signature-block
# finding and cut the review from 3 Missing Context items to 1.
#
# The conversational skills (drafting, compliance_check, contract_generation)
# keep the injection — multi-turn continuity is the point there. legal_research
# never reaches this node (it sets llm_response itself) and does its own
# history handling via memory/conversation_store.
_HISTORY_FREE_TASK_TYPES = frozenset({"contract_review"})


def _build_context(chunks: list[dict]) -> str:
    """Format retrieved chunks as numbered context."""
    if not chunks:
        return "No documents retrieved."
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(
            f"[{i}] {c.get('doc_title', 'Unknown')} (doc_id: {c.get('doc_id', '?')})\n"
            f"{c.get('text', '')}"
        )
    return "\n\n---\n\n".join(parts)


@traced("llm_caller", kind="LLM")
def llm_caller(state: LegalAgentState) -> LegalAgentState:
    """Call Ollama with context + request. temperature=0.0 always."""
    if state.get("llm_response") and not state.get("messages"):
        logger.info("[llm_caller] llm_response already set by agent — skipping")
        return state

    settings = get_settings()

    # Reset per-turn outputs so a turn that doesn't trigger them doesn't carry
    # a PRIOR turn's value forward. State persists per thread via the Redis
    # checkpointer, and api/routes/query.py's initial_state does not seed
    # context_truncated/token_usage (unlike memory_degraded, which is an
    # OR-accumulator written by several nodes and must survive mid-turn — a
    # different shape, hence not reset here). Without this, one over-budget
    # review leaves context_truncated set and every later healthy turn in that
    # session shows a false "part of this document was not sent" banner.
    # Symmetric with skills/legal_research/legal_research.py's reset. Must sit
    # AFTER the early-return above: on a chat turn legal_research already set
    # these, and llm_caller then hits that early-return — resetting before it
    # would wipe the chat path's flag and silently undo that turn's work.
    state["context_truncated"] = None
    state["token_usage"] = None

    chunks = state.get("retrieved_chunks", [])
    context = _build_context(chunks)

    skill_messages = state.get("messages", [])
    task_type = state.get("task_type", "")
    chat_history = state.get("chat_history", []) or []

    if chat_history and task_type in _HISTORY_FREE_TASK_TYPES:
        logger.info(
            "[llm_caller] task_type=%s — suppressing %d chat_history message(s)",
            task_type, len(chat_history),
        )
        chat_history = []

    if skill_messages:
        base = list(skill_messages)
        if base and base[-1]["role"] == "user":
            base[-1] = {
                "role": "user",
                "content": f"Context:\n{context}\n\n{base[-1]['content']}",
            }
    else:
        base = [
            {"role": "system", "content": _DEFAULT_SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n{context}\n\nRequest: {state['request']}"},
        ]

    # Inject chat_history between the system message (if any) and the rest.
    if base and base[0].get("role") == "system":
        messages = [base[0], *chat_history, *base[1:]]
    else:
        messages = [*chat_history, *base]

    # Announce BEFORE the call. uvicorn writes its access line only when the
    # response completes, and every other log here fires post-call — so an
    # in-flight turn was completely invisible: "queued behind another tenant on
    # the shared Ollama" and "wedged" looked identical in `docker logs`.
    chars = sum(len(m.get("content", "")) for m in messages)

    # contract_review has no input cap, so past some document size Ollama
    # middle-drops the prompt — which removes exactly the playbook/MSA and
    # yields a confidently under-grounded review with no log line at all.
    # DETECTION ONLY, on purpose: choosing what to sacrifice in a review is a
    # real design question, and the chat path's answer (cut the document) is
    # precisely the bug this change exists to fix. Report it and let the review
    # proceed; a visible degraded answer beats a silent wrong one.
    headroom_chars = int(
        (settings.ollama_num_ctx - settings.ollama_num_predict_review)
        * settings.est_chars_per_token
    )
    if chars > headroom_chars:
        logger.error(
            "[llm_caller] review input %d chars EXCEEDS headroom %d "
            "(num_ctx=%d - num_predict_review=%d at %.2f chars/token) — "
            "the model will silently drop part of this prompt",
            chars, headroom_chars, settings.ollama_num_ctx,
            settings.ollama_num_predict_review, settings.est_chars_per_token,
        )
        state["context_truncated"] = {
            "doc_chars": chars,
            "kept_chars": headroom_chars,
            "kept_pct": (headroom_chars * 100 // chars) if chars else 0,
        }
        record_degradation(
            CONTEXT_TRUNCATED,
            announced=True,
            detail=f"kept {state['context_truncated']['kept_pct']}% of the document",
        )

    logger.info(
        "[llm_caller] -> ollama model=%s task=%s messages=%d chars=%d url=%s",
        settings.llm_model, state.get("task_type", "?"), len(messages), chars,
        settings.ollama_base_url,
    )
    started = time.monotonic()

    try:
        response = httpx.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.llm_model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_ctx": settings.ollama_num_ctx,
                    # Bounded so a repetition loop truncates instead of
                    # running to the context limit (see config.py).
                    "num_predict": settings.ollama_num_predict_review,
                },
            },
            timeout=600.0,
        )
        response.raise_for_status()
        data = response.json()
        content = data["message"]["content"]
        state["llm_response"] = content

        # Same value already handed to set_gen_attributes below; routed to state
        # so the pane can show real token counts. Not a second extraction.
        state["token_usage"] = ollama_usage(data)

        set_gen_attributes(
            input=messages,
            output=content,
            model=settings.llm_model,
            usage=ollama_usage(data),
            timings=ollama_timings(data),
            metadata={
                "task_type": state.get("task_type", ""),
                "chunks_count": len(chunks),
                "temperature": 0.0,
            },
        )
        logger.info(
            "[llm_caller] <- ollama %d chars in %.1fs", len(content), time.monotonic() - started
        )
    except Exception as e:
        logger.error(
            "[llm_caller] LLM call FAILED after %.1fs: %s", time.monotonic() - started, e
        )
        mark_failed(LLM_CALL_FAILED, exc=e, detail=e.__class__.__name__)
        state["llm_response"] = f"Error: LLM call failed — {e}"

    return state
