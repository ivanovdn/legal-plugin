# skills/legal_research/context.py
"""Assemble the doc-chat prompt's context, within budget.

Four sources feed a chat turn: the prior review for this document, the durable
per-(document, attorney) conversation, the playbook + governing MSA grounding,
and the document itself. Every read is best-effort — a memory or grounding
failure degrades the turn, never breaks it — and the budget cap truncates only
the document, never the grounding.

Testing note — patch the module whose globals the call path resolves through.
legal_research.py re-imports the seven functions above (_load_prior_review_block,
_load_prior_conversation, _needs_grounding, _build_chat_grounding,
_cap_chat_context, build_context_breakdown, compressible_history), so
those names exist on BOTH modules bound to the same object; get_settings hits
the same hazard by a different route (both modules independently do `from
config import get_settings` — same object, not a re-export). Patch *this*
module when calling one of these SHARED names directly; patch legal_research
when driving one through legal_research(state), since _run_doc_chat resolves
them in the entry module's globals. Aiming at the wrong module silently no-ops
for any of them.

A separate, NOT-shared set of names moved out of legal_research.py entirely and
was never re-imported there: load_latest_review, load_recent,
detect_contract_type, load_playbook_bundle, attach_parent_msa,
_reconcile_review_with_doc, latest_to_id, load_segments, row_lengths_after.
Patching those via the entry module fails loudly with AttributeError instead
of silently no-oping.
"""
import logging
import re

from config import get_settings
from graph.state import LegalAgentState
from memory.conversation_store import load_recent, row_lengths_after
from memory.conversation_summary import latest_to_id, load_segments
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

    Condensed segments first (oldest-first, as system messages), then the
    verbatim rows after the highest condensed id. Splicing them into the history
    slot puts the summaries BELOW every grounding system message and above the
    user's question, so recalled discussion never outranks the live document,
    playbook, MSA or prior review — which is what the block's own header claims.

    The verbatim floor is latest_to_id (ALL segments), not the injected window:
    a segment that has aged out of the prompt still condensed its rows, and
    replaying them verbatim would undo that.

    Empty when disabled, keys missing, or on a store-read failure (which flags
    memory_degraded) — memory must never break the chat turn.
    """
    settings = get_settings()
    if not settings.conversation_store_enabled:
        return []
    document_id = state.get("document_id", "")
    attorney_id = state.get("user_id", "")
    if not document_id or not attorney_id:
        return []
    boundary = 0
    summaries: list[dict] = []
    if settings.compaction_enabled:
        try:
            boundary = latest_to_id(document_id, attorney_id)
            summaries = [
                {"role": "system", "content": s["content"]}
                for s in load_segments(
                    document_id, attorney_id, settings.compaction_max_injected_segments
                )
            ]
        except Exception as e:
            # A summary-read failure must not cost the attorney their raw
            # conversation: conversation_store is a different table and is very
            # likely fine. Fall back to no floor and no summaries — which
            # replays the rows verbatim, so nothing is lost and nothing is
            # duplicated (the summaries that would have covered them did not
            # load either).
            logger.error("[legal_research] summary load failed: %s", e)
            state["memory_degraded"] = True
            boundary, summaries = 0, []
    try:
        verbatim = load_recent(
            document_id, attorney_id, settings.conversation_max_messages, boundary,
        )
    except Exception as e:
        logger.error("[legal_research] prior-conversation load failed: %s", e)
        state["memory_degraded"] = True
        return []
    return [*summaries, *verbatim]


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


_MSA_BLOCK_START = "--- GOVERNING MSA ("
_MSA_BLOCK_END = "--- END GOVERNING MSA ---"
_MSA_CUT_NOTE = "\n\n[MSA truncated to fit the context budget]"


def _trim_msa_to_fit(messages: list[dict], overflow: int) -> int:
    """Give up governing-MSA text to keep the document whole. Returns chars freed.

    This reverses the old rule that grounding is never truncated. That rule was
    written when msa_max_chars was 24,000 and the MSA therefore could not itself
    be the reason a turn overflowed; now that it is a ceiling rather than a size,
    it can be — and between the two, the contract under review wins. The MSA is
    reference material we already choose to truncate; the document is the source
    of truth, and its cut is a TAIL cut, so what goes is liability, indemnity,
    termination, governing law and the signature blocks.

    But the MSA is spent ONLY when spending it actually saves the document. If
    the overflow is bigger than the whole block, the contract is getting cut
    either way, and surrendering the comparison as well buys nothing — so we
    leave it whole and fall through to the document cut, exactly as before. That
    makes this strictly non-regressive: the only turns whose behaviour changes
    are the ones where the document now survives intact.

    The playbook is never touched. It is the ceiling on legal judgment, not
    reference material.
    """
    for m in messages:
        if m.get("role") != "system" or _MSA_BLOCK_START not in m.get("content", ""):
            continue
        content = m["content"]
        head_end = content.index("\n", content.index(_MSA_BLOCK_START)) + 1
        tail_start = content.rindex(_MSA_BLOCK_END)
        msa_text = content[head_end:tail_start]
        # Removing n chars of MSA costs len(note) back, so saving `overflow`
        # needs overflow + len(note) available. Short of that, spending it is a
        # loss on both sides.
        if len(msa_text) < overflow + len(_MSA_CUT_NOTE):
            return 0
        keep = len(msa_text) - overflow - len(_MSA_CUT_NOTE)
        m["content"] = content[:head_end] + msa_text[:keep] + _MSA_CUT_NOTE + content[tail_start:]
        freed = len(content) - len(m["content"])
        logger.warning(
            "[legal_research] trimmed the governing MSA by %d chars (%d -> %d) to keep "
            "the document whole", freed, len(msa_text), keep,
        )
        return freed
    return 0


def msa_chars_sent(messages: list[dict]) -> int:
    """Size of the governing-MSA block as it will actually be sent, post-trim.

    The caller holds `msa_block` as a local, but _cap_chat_context trims the
    MESSAGE, so that local is stale the moment a trim happens. Reporting it would
    put a 73,152-char MSA row in the pane beside a 36,325-char reality, and a
    total over budget with no truncation notice — a counter that contradicts
    itself, which is the exact defect the sideload caught twice before.
    """
    return sum(len(m["content"]) for m in messages
               if m.get("role") == "system" and _MSA_BLOCK_START in m.get("content", ""))


def _cap_chat_context(messages: list[dict], uploaded_text: str, request: str) -> dict | None:
    """If total assembled content exceeds the budget, truncate ONLY the document
    portion of the trailing user message — never the grounding. Mutates messages
    in place.

    Returns None when nothing was cut, else what was lost:
        {"doc_chars": int, "kept_chars": int, "kept_pct": int}

    The return value exists because a log line is invisible to the attorney. A
    truncated contract means the answer may be legally unsound — measured
    2026-08-21, a real MSA turn kept only 58% of the document at the old budget
    and 23% with a full history window, and the cut is a TAIL cut, so what goes
    missing is liability, indemnity, term/termination, governing law and the
    signature blocks. The caller routes this to the report so the pane can say
    so. See docs/superpowers/specs/2026-08-21-context-budget-design.md.
    """
    budget = get_settings().chat_context_max_chars
    total = sum(len(m["content"]) for m in messages)
    if total <= budget:
        return None
    # The MSA gives way first. Only what it cannot cover comes out of the contract.
    total -= _trim_msa_to_fit(messages, total - budget)
    if total <= budget:
        return None
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
    doc_chars = len(uploaded_text)
    return {
        "doc_chars": doc_chars,
        "kept_chars": keep,
        "kept_pct": (keep * 100 // doc_chars) if doc_chars else 0,
    }


def compressible_history(state: LegalAgentState) -> tuple[int, int]:
    """(messages, characters) that a Condense action would actually condense —
    past the last segment AND outside the verbatim window.

    BOTH numbers, because the two floors this feeds are measured differently: the
    manual button only asks whether anything exists at all, while automatic firing
    has to decide whether a 10-30s call is worth making, and that is a question
    about SIZE. Returning only a count is what let a 96%-of-budget history sit
    uncondensed because it happened to be three messages.

    Best-effort: (0, 0) on any failure, and when compaction is off. This feeds a
    UI affordance, so a store hiccup must cost the attorney a button, never a
    turn. It does NOT flag memory_degraded — that is reserved for reads the
    answer depends on.
    """
    settings = get_settings()
    if not settings.compaction_enabled:
        return 0, 0
    document_id = state.get("document_id", "")
    attorney_id = state.get("user_id", "")
    if not document_id or not attorney_id:
        return 0, 0
    try:
        boundary = latest_to_id(document_id, attorney_id)
        lengths = row_lengths_after(document_id, attorney_id, boundary)
    except Exception as e:
        logger.warning("[legal_research] compressible-history read failed: %s", e)
        return 0, 0
    # Drop the newest keep_recent_messages: compaction leaves those verbatim, so
    # their characters are not reclaimable and must not arm anything.
    keep = settings.compaction_keep_recent_messages
    compressible = lengths[: max(0, len(lengths) - keep)]
    return len(compressible), sum(compressible)


# Display order. Only `history` is compactable: the document is the source of
# truth, the playbook is the ceiling, and neither is ever summarised.
_BREAKDOWN_PARTS = ("document", "playbook", "msa", "review", "history", "system")


def build_context_breakdown(
    *,
    doc_chars: int,
    playbook_chars: int,
    msa_chars: int,
    review_chars: int,
    history_chars: int,
    system_chars: int,
    compressible_messages: int,
    compressible_chars: int,
) -> dict:
    """What this turn actually spent, as the pane's counter renders it.

    Measured, never predicted: the caller passes the POST-truncation document
    size, so the counter reports what was sent rather than what was asked for.
    A forecast is impossible anyway — _needs_grounding keys off the question's
    wording, so the same contract costs 49k or 131k depending on what is asked.

    history_chars covers everything in the history slot, injected summary
    segments included. That is deliberate: summaries are a real prompt cost, and
    watching the history line drop after a compaction is the attorney's proof
    the feature did something.
    """
    settings = get_settings()
    budget = settings.chat_context_max_chars
    cpt = settings.est_chars_per_token
    sizes = {
        "document": doc_chars, "playbook": playbook_chars, "msa": msa_chars,
        "review": review_chars, "history": history_chars, "system": system_chars,
    }
    total = sum(sizes.values())
    parts = [
        {
            "key": key,
            "chars": sizes[key],
            "tokens": int(sizes[key] / cpt),
            "pct": (sizes[key] * 100 // budget) if budget else 0,
            "compactable": key == "history",
        }
        for key in _BREAKDOWN_PARTS
    ]
    pct = (total * 100 // budget) if budget else 0
    # Both conditions, always. The threshold alone would offer a no-op on a
    # short conversation with a huge document; compressible history alone
    # would nag on every routine chat.
    can_compact = bool(
        settings.compaction_enabled
        and compressible_messages > 0
        and pct >= settings.compaction_warn_pct
    )
    return {
        "budget_chars": budget,
        "budget_tokens": int(budget / cpt),
        "chars_per_token": cpt,
        "total_chars": total,
        "total_tokens": int(total / cpt),
        "pct": pct,
        "warn_pct": settings.compaction_warn_pct,
        "can_compact": can_compact,
        # Built FROM can_compact, not alongside it, so "auto is a narrowing of the
        # button" is structural and cannot drift. ONE floor, in characters, because
        # every question here is a question about size: can_compact already
        # establishes that there is PRESSURE (pct past the warn line), so the only
        # thing left to ask is whether the pool is big enough for a segment to come
        # out smaller than the rows it replaces. That is compaction_auto_min_chars,
        # and it is derived from segment overhead rather than from a budget — see
        # config.py for why the two floors this replaced could not arm until after
        # the contract had already been truncated.
        #
        # No churn loop results from the low floor: a successful compaction drops
        # pct back under the warn line, which turns can_compact off, so nothing can
        # re-arm until real pressure returns.
        "auto_compact": bool(
            can_compact
            and settings.compaction_auto
            and compressible_chars >= settings.compaction_auto_min_chars
        ),
        "compressible_messages": compressible_messages,
        "parts": parts,
    }
