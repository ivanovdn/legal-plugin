# skills/contract_review/contract_review.py
"""Contract review — applies the Trinetix legal-team playbook clause-by-clause.

Loads the per-type playbook bundle (skills/contract_review/playbook/) via
`skills.base.load_bundle`. Bundle = global rules (role, principles, risk
rating, approval matrix, output format, AI review procedure, external
comments, contract selection, clause bank, no-signature checklist) + the
per-type SKILL.md + per-type clause matrix. See docs/playbook_cross_reference.md.

Contract type is detected from the uploaded text via a cheap heading-keyword
heuristic. When detection is ambiguous, defaults to NDA (the most-conservative
bundle) and logs the ambiguity so a future iteration can add an LLM fallback or
a user-facing override.
"""

import logging
import re
from pathlib import Path

from graph.state import LegalAgentState
from skills.base import load_bundle

logger = logging.getLogger(__name__)

_SKILL_DIR = Path(__file__).parent
_PLAYBOOK_DIR = _SKILL_DIR / "playbook"

# Heading/keyword cues for type detection. Search the first 4K chars of the
# doc (headings live near the top). All matchers are case-insensitive whole-word
# or anchored phrases — we want strong signal, not partial matches inside
# random sentences.
#
# Order matters only for tie-breaking ties, which the heuristic should rarely
# hit; if it does, the first type whose regex matches wins.
_TYPE_PATTERNS: tuple[tuple[str, tuple[re.Pattern, ...]], ...] = (
    (
        "baa",
        (
            re.compile(r"\bbusiness associate agreement\b", re.I),
            re.compile(r"\bhipaa\b", re.I),
            re.compile(r"\bprotected health information\b", re.I),
            re.compile(r"\bphi\b"),  # uppercase only — avoid matching "Phi" inside random words
        ),
    ),
    (
        "msa",
        (
            re.compile(r"\bmaster\s+services?\s+agreement\b", re.I),
            re.compile(r"\bmsa\b", re.I),
        ),
    ),
    (
        "sow",
        (
            re.compile(r"\bstatement\s+of\s+work\b", re.I),
            re.compile(r"\bwork\s+order\b", re.I),
            re.compile(r"\bsow\b", re.I),
        ),
    ),
    (
        "nda",
        (
            re.compile(r"\bnon[-\s]?disclosure\s+agreement\b", re.I),
            re.compile(r"\bmutual\s+nda\b", re.I),
            re.compile(r"\bmnda\b", re.I),
            re.compile(r"\bconfidentiality\s+agreement\b", re.I),
            re.compile(r"\bnda\b", re.I),
        ),
    ),
)

_DEFAULT_TYPE = "nda"

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


def _detect_contract_type(text: str) -> tuple[str, bool]:
    """Detect contract type from text. Returns (type, was_ambiguous).

    Counts pattern hits in the first 4K characters and picks the type with the
    most hits. Returns (_DEFAULT_TYPE, True) when no pattern matches.
    """
    head = text[:4000]
    scores: dict[str, int] = {}
    for ctype, patterns in _TYPE_PATTERNS:
        scores[ctype] = sum(len(p.findall(head)) for p in patterns)

    best_type = max(scores, key=lambda t: scores[t])
    best_score = scores[best_type]
    if best_score == 0:
        return _DEFAULT_TYPE, True
    return best_type, False


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
    contract_type, was_ambiguous = _detect_contract_type(detect_source)
    state["contract_type_detected"] = contract_type
    if was_ambiguous:
        logger.warning(
            "[contract_review] type detection ambiguous — defaulting to %s. "
            "Consider adding an LLM fallback or a user-facing override.",
            contract_type,
        )

    playbook = load_bundle(_PLAYBOOK_DIR, contract_type)

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

    attorney_notes = (state.get("attorney_notes") or "").strip()
    if attorney_notes:
        user_content += (
            f"\n\n--- ATTORNEY REVIEW NOTES (incorporate these changes) ---\n"
            f"{attorney_notes}"
        )

    state["messages"] = [
        {"role": "system", "content": playbook},
        {"role": "system", "content": _OUTPUT_CONSTRAINTS},
        {"role": "user", "content": user_content},
    ]

    logger.info(
        "[contract_review] prepared: type=%s ambiguous=%s uploaded=%d chars rag=%s playbook=%d chars",
        contract_type,
        was_ambiguous,
        len(uploaded_text),
        "yes" if state["retrieval_query"] else "no",
        len(playbook),
    )
    return state
