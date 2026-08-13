# skills/legal_research/context.py
"""Assemble the doc-chat prompt's context, within budget.

Four sources feed a chat turn: the prior review for this document, the durable
per-(document, attorney) conversation, the playbook + governing MSA grounding,
and the document itself. Every read is best-effort — a memory or grounding
failure degrades the turn, never breaks it — and the budget cap truncates only
the document, never the grounding.
"""
import logging
import re

from config import get_settings
from graph.state import LegalAgentState
from memory.conversation_store import load_recent
from memory.review_store import load_latest_review
from skills.grounding import (
    attach_parent_msa,
    detect_contract_type,
    load_playbook_bundle,
)
from skills.legal_research.prompts import _CHAT_MSA_NOTE
from skills.legal_research.review_recall import (
    _reconcile_review_with_doc,
    _strip_redlines_section,
)

logger = logging.getLogger(__name__)


def _load_prior_review_block(state: LegalAgentState, uploaded_text: str) -> str:
    """Latest stored review for this document, as a system block. Empty string
    when none exists. On a store-read failure, flags memory_degraded and returns
    empty — tracing/memory must never break the chat turn.

    Reconciles the recalled review against the current document (uploaded_text):
    placeholder findings the document proves were filled after the review are
    dropped, so chat does not report an already-filled field as unfilled. A
    reconciliation error injects the review unchanged (never fails the turn) and
    does NOT flag memory_degraded — that is reserved for real store failures."""
    document_id = state.get("document_id", "")
    if not document_id:
        return ""
    try:
        latest = load_latest_review(document_id)
    except Exception as e:
        logger.error("[legal_research] prior-review load failed: %s", e)
        state["memory_degraded"] = True
        return ""
    if not latest:
        return ""
    review_text = _strip_redlines_section(latest["markdown"])
    if uploaded_text:
        try:
            review_text, _filled = _reconcile_review_with_doc(review_text, uploaded_text)
        except Exception as e:
            logger.warning(
                "[legal_research] review reconciliation failed: %s — injecting review unchanged", e
            )
    return (
        "--- PRIOR REVIEW (most recent, this document) ---\n"
        "Answer recall questions from this review; do not re-derive or contradict it.\n\n"
        f"{review_text}\n"
        "--- END PRIOR REVIEW ---"
    )


def _load_prior_conversation(state: LegalAgentState) -> list[dict]:
    """Durable per-(document, attorney) chat history for the doc-chat prompt.
    Empty when disabled, keys missing, or on a store-read failure (which flags
    memory_degraded) — memory must never break the chat turn."""
    settings = get_settings()
    if not settings.conversation_store_enabled:
        return []
    document_id = state.get("document_id", "")
    attorney_id = state.get("user_id", "")
    if not document_id or not attorney_id:
        return []
    try:
        return load_recent(
            document_id, attorney_id, settings.conversation_max_messages,
        )
    except Exception as e:
        logger.error("[legal_research] prior-conversation load failed: %s", e)
        state["memory_degraded"] = True
        return []


_GROUNDING_TRIGGER_RE = re.compile(
    r"""
    # Edit / action stems
    chang|edit|modif|revis|rewrit|redraft|redline|amend|soften|tighten|loosen|
    strengthen|\bfill|insert|\badd\b|remov|delet|replac|updat|\bfix|draft|shorten|
    extend|adjust
    |
    # Position / judgment stems
    should|acceptab|standard|policy|playbook|fallback|position|\bmarket|allow|
    complian|\bcomply|\brisk|aggressiv|unusual|favorab|unfavorab|protect|\bweak|
    negotiat|pushback|concession|deviat
    |
    # Cross-doc / MSA stems
    \bmsa\b|master\s+service|\bparent\b|governing|precedenc|conflict|inconsist|
    overrid|incorporat|breach|subject\s+to
    |
    # Clause names — legal judgment calls
    indemn|liabilit|warrant|confidential|intellectual\s+property|\bip\b|ownership|
    terminat|jurisdiction|governing\s+law|non-compet|non-solicit|penalt|\bsla\b|
    service\s+level|\bcap\b|limitation
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _needs_grounding(question: str) -> bool:
    """True when a chat turn needs the firm playbook / governing MSA attached —
    i.e. it asks for an edit/redline, a firm position/standard, a cross-document
    (MSA) judgment, or names a clause whose treatment is a legal-judgment call.
    Biased toward True: a plain factual extraction ('who signs?', 'what is the
    effective date?') returns False and takes the lean, fast path. This is a
    zero-LLM heuristic — when in doubt it attaches (never under-grounds)."""
    return bool(_GROUNDING_TRIGGER_RE.search(question))


def _build_chat_grounding(state: LegalAgentState, uploaded_text: str) -> tuple[str, str]:
    """(playbook_bundle, msa_block) for the chat path. Empty strings on failure —
    grounding must never break the chat turn. MSA only for SOWs."""
    playbook = ""
    msa_block = ""
    try:
        contract_type, _ = detect_contract_type(uploaded_text)
        playbook = load_playbook_bundle(contract_type)
        if contract_type == "sow":
            client_id = (state.get("filters") or {}).get("client_id", "")
            parent = attach_parent_msa(uploaded_text, client_id, get_settings().msa_max_chars)
            if parent:
                title, msa_text = parent
                msa_block = (
                    f"{_CHAT_MSA_NOTE}\n\n--- GOVERNING MSA ({title}) ---\n"
                    f"{msa_text}\n--- END GOVERNING MSA ---"
                )
    except Exception as e:
        logger.warning("[legal_research] chat grounding failed: %s — answering ungrounded", e)
    return playbook, msa_block


def _cap_chat_context(messages: list[dict], uploaded_text: str, request: str) -> None:
    """If total assembled content exceeds the budget, truncate ONLY the document
    portion of the trailing user message — never the grounding. Mutates messages
    in place; marks + logs the truncation. Crude on purpose (Phase 3)."""
    budget = get_settings().chat_context_max_chars
    total = sum(len(m["content"]) for m in messages)
    if total <= budget:
        return
    overflow = total - budget
    keep = max(0, len(uploaded_text) - overflow - len("\n\n[document truncated for context budget]"))
    truncated_doc = uploaded_text[:keep] + "\n\n[document truncated for context budget]"
    messages[-1]["content"] = (
        f"--- ATTACHED DOCUMENT (the source of truth — answer from this) ---\n"
        f"{truncated_doc}\n"
        f"--- END ATTACHED DOCUMENT ---\n\n"
        f"User request: {request}"
    )
    logger.warning("[legal_research] chat context %d > budget %d — truncated document to %d chars",
                   total, budget, keep)
