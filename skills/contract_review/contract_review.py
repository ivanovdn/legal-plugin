# skills/contract_review/contract_review.py
"""Contract review — applies the Trinetix legal-team playbook clause-by-clause.

Loads the per-type playbook bundle (skills/contract_review/playbook/) via
`skills.grounding.load_playbook_bundle`. Bundle = global rules (role, principles,
risk rating, approval matrix, output format, AI review procedure, external
comments, contract selection, clause bank, no-signature checklist) + the
per-type SKILL.md + per-type clause matrix. See docs/playbook_cross_reference.md.

Contract type is detected from the uploaded text via a cheap heading-keyword
heuristic in `skills.grounding.detect_contract_type`. When detection is ambiguous,
defaults to NDA (the most-conservative bundle) and logs the ambiguity so a future
iteration can add an LLM fallback or a user-facing override.
"""

import logging

from langfuse.decorators import observe, langfuse_context

from graph.state import LegalAgentState
from rag.related_docs import get_parent_msa
from skills.grounding import detect_contract_type, load_playbook_bundle

logger = logging.getLogger(__name__)

# Runtime directive appended to the system message after the canonical bundle.
# The team's required output format leaves one detail open to interpretation:
# nothing in `shared_operating_rules.md` explicitly forbids combining multiple
# findings into one Suggested Redlines row. The model sometimes coalesces
# adjacent placeholders (e.g. "Preamble / Effective Date" + "Preamble / Parties"
# → one row "Insert [Legal Name], [Address], [Month] [Date], [Year]"), which
# breaks per-card redline rendering in the Word add-in. This constraint pins
# the one-row-per-finding rule without touching the canonical bundle.
_OUTPUT_CONSTRAINTS = """OUTPUT CONSTRAINTS (in addition to the playbook above):

1. The "Suggested Redlines / Fallbacks" table must contain ONE ROW PER FINDING. \
Each row's "Clause / section" cell must match the corresponding finding's \
"Clause / section" cell verbatim (including the full "Parent / Child" path).

2. Do NOT combine multiple findings into a single redline row. If "Preamble / \
Effective Date" and "Preamble / Parties" are both findings, emit two separate \
rows — one whose "Proposed wording or instruction" addresses ONLY the date, \
and another whose wording addresses ONLY the parties.

3. Each redline's "Proposed wording or instruction" must address only the issue \
named in its "Clause / section". Don't include text related to other findings.

4. A finding listed under "Key Findings" with a Yellow or Red rating, or under \
"Red and Missing Context Items", must have at least one corresponding row in \
"Suggested Redlines / Fallbacks"."""

# Max chars of MSA text injected into a SOW review. Guards the local LLM's
# context window so a huge MSA can't crowd out the SOW + playbook. Promote to
# config.Settings when scaling past the one-MSA demo.
_MSA_MAX_CHARS = 24000

# Added (as the LAST system message) only when a governing MSA is attached to a
# SOW review. Deliberately STRUCTURAL and model-neutral: it orchestrates a
# document-to-document comparison and defers ALL legal judgment to the playbook —
# it encodes no positions of its own (SKILL.md is the ceiling). The SOW playbook
# already REQUIRES this check (sow/SKILL.md: "conflicts with MSA"; "SOW must be
# pursuant to and subject to the MSA. Red if SOW overrides core MSA protections
# without Legal approval"; MSA-001 precedence rule) and names the MSA as a
# required input — this directive just supplies that input and mirrors those
# rules verbatim. Rule 3 stops the local LLM hallucinating "the MSA says X".
_MSA_COMPARISON_DIRECTIVE = """GOVERNING MSA COMPARISON — this SOW is issued \
under the Master Services Agreement included below as "GOVERNING MSA":

1. The MSA is the parent framework and the SOW is pursuant to and subject to it. \
Per the playbook, SOW-specific terms control only for that SOW; SOW deviations \
are acceptable only if limited to that SOW and approved by the relevant owner.

2. Flag, as findings, any SOW term that (a) overrides a core MSA protection \
without approval, (b) changes the MSA's order of precedence or applies broadly \
beyond that SOW, or (c) is required by the MSA but missing or inconsistent in \
the SOW (e.g. the MSA date/version reference, or terms governed by the MSA such \
as payment, IP ownership, confidentiality, or the liability cap). Cite the \
relevant MSA clause in the Issue, and apply the SOW playbook's risk rating and \
approval rules as usual.

3. Do NOT invent MSA terms. Base every MSA-conflict finding only on text present \
in the GOVERNING MSA below; if the MSA is silent on a point, say so rather than \
assuming."""


def _detect_contract_type(text: str) -> tuple[str, bool]:
    """Thin shim kept for backward compatibility with existing test imports.

    Delegates entirely to `skills.grounding.detect_contract_type` — do not add
    logic here; edit grounding.py instead.
    """
    return detect_contract_type(text)


def _extract_uploaded_text(state: LegalAgentState) -> str:
    """Extract contract text from uploaded_docs in state."""
    docs = state.get("uploaded_docs", [])
    if not docs:
        return ""
    parts = []
    for doc in docs:
        if isinstance(doc, dict):
            parts.append(doc.get("text", ""))
        elif hasattr(doc, "text"):
            parts.append(doc.text)
    return "\n\n".join(parts)


@observe(name="contract_review", capture_input=False, capture_output=False)
def contract_review(state: LegalAgentState) -> LegalAgentState:
    """Prepare state for clause analysis using the per-type playbook bundle.

    If uploaded contract text is available (from Chainlit file upload or the
    Word add-in), it's included in the user message and the contract type is
    detected from the text. Otherwise the request goes to RAG retrieval and we
    default to the NDA bundle (the most-conservative; the request itself
    determines the actual content the user is asking about).
    """
    request = state["request"]
    uploaded_text = _extract_uploaded_text(state)

    # Detect contract type from the uploaded doc; if no doc, the request itself
    # is the only signal we have (rare path — usually the Word/Chainlit flows
    # always upload). Falls back to NDA.
    detect_source = uploaded_text or request
    contract_type, was_ambiguous = detect_contract_type(detect_source)
    state["contract_type_detected"] = contract_type
    if was_ambiguous:
        logger.warning(
            "[contract_review] type detection ambiguous — defaulting to %s. "
            "Consider adding an LLM fallback or a user-facing override.",
            contract_type,
        )

    # Surface detection on the Langfuse trace. This is the signal that was
    # invisible when an MSA was silently reviewed as a SOW (audit Dimension 7) —
    # the skill has no @observe span, so without this the detected type only
    # leaks out via the final report payload. Tracing must never break the skill.
    try:
        langfuse_context.update_current_trace(
            metadata={
                "contract_type_detected": contract_type,
                "contract_type_ambiguous": was_ambiguous,
            },
        )
    except Exception:  # pragma: no cover - observability is best-effort
        pass

    playbook = load_playbook_bundle(contract_type)

    # Build user message with contract text if available
    if uploaded_text:
        user_content = (
            f"{request}\n\n"
            f"--- CONTRACT TEXT ---\n"
            f"{uploaded_text}\n"
            f"--- END CONTRACT TEXT ---"
        )
        # No need for RAG retrieval when contract is uploaded
        state["retrieval_query"] = ""
    else:
        user_content = request
        state["retrieval_query"] = request

    # SOW review: pull the governing MSA from Qdrant and attach it so the SOW is
    # reviewed against its parent. Strictly additive — any failure degrades to a
    # standalone SOW review. SOW path with an uploaded doc only.
    msa_attached = False
    msa_doc_title = ""
    if contract_type == "sow" and uploaded_text:
        client_id = (state.get("filters") or {}).get("client_id", "")
        try:
            parent = get_parent_msa(client_id)
        except Exception:  # retrieval must never break the review
            logger.exception(
                "[contract_review] parent-MSA lookup failed — reviewing SOW standalone"
            )
            parent = None
        if parent:
            msa_doc_title, msa_text = parent
            if len(msa_text) > _MSA_MAX_CHARS:
                logger.warning(
                    "[contract_review] MSA %r is %d chars — truncating to %d for review",
                    msa_doc_title, len(msa_text), _MSA_MAX_CHARS,
                )
                msa_text = (
                    msa_text[:_MSA_MAX_CHARS]
                    + f"\n\n[MSA truncated to {_MSA_MAX_CHARS} chars for review]"
                )
            user_content += (
                f"\n\n--- GOVERNING MSA ({msa_doc_title}) ---\n"
                f"{msa_text}\n"
                f"--- END GOVERNING MSA ---"
            )
            msa_attached = True
            logger.info(
                "[contract_review] attached governing MSA %r (%d chars)",
                msa_doc_title, len(msa_text),
            )
        else:
            logger.info(
                "[contract_review] no governing MSA on file for client_id=%s — "
                "reviewing SOW standalone",
                client_id,
            )

    attorney_notes = (state.get("attorney_notes") or "").strip()
    if attorney_notes:
        user_content += (
            f"\n\n--- ATTORNEY REVIEW NOTES (incorporate these changes) ---\n"
            f"{attorney_notes}"
        )

    system_messages = [
        {"role": "system", "content": playbook},
        {"role": "system", "content": _OUTPUT_CONSTRAINTS},
    ]
    if msa_attached:
        system_messages.append({"role": "system", "content": _MSA_COMPARISON_DIRECTIVE})
    state["messages"] = system_messages + [{"role": "user", "content": user_content}]

    # Surface MSA attachment on the trace (best-effort; never breaks the skill).
    try:
        langfuse_context.update_current_trace(
            metadata={"msa_attached": msa_attached, "msa_doc_title": msa_doc_title},
        )
    except Exception:  # pragma: no cover - observability is best-effort
        pass

    logger.info(
        "[contract_review] prepared: type=%s ambiguous=%s uploaded=%d chars rag=%s playbook=%d chars",
        contract_type,
        was_ambiguous,
        len(uploaded_text),
        "yes" if state["retrieval_query"] else "no",
        len(playbook),
    )
    return state
