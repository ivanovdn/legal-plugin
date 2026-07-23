# skills/grounding.py
"""Shared contract grounding — type detection, playbook bundle, parent-MSA attach.

Single source of truth used by BOTH surfaces: the Findings path
(skills/contract_review) and the Chat path (skills/legal_research). Keeping it
here is what prevents the two surfaces from drifting back into asymmetry.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from rag.related_docs import get_parent_msa
from skills.base import load_bundle
from config import get_settings
from memory.preferences import load_preferences

logger = logging.getLogger(__name__)

_PLAYBOOK_DIR = Path(__file__).parent / "contract_review" / "playbook"

_TYPE_PATTERNS: tuple[tuple[str, tuple[re.Pattern, ...]], ...] = (
    ("baa", (
        re.compile(r"\bbusiness associate agreement\b", re.I),
        re.compile(r"\bhipaa\b", re.I),
        re.compile(r"\bprotected health information\b", re.I),
        re.compile(r"\bphi\b"),  # uppercase only — avoid matching "Phi" inside random words
    )),
    ("msa", (
        re.compile(r"\bmaster\s+services?\s+agreement\b", re.I),
        re.compile(r"\bmsa\b", re.I),
    )),
    ("sow", (
        re.compile(r"\bstatement\s+of\s+work\b", re.I),
        re.compile(r"\bwork\s+order\b", re.I),
        re.compile(r"\bsow\b", re.I),
    )),
    ("nda", (
        re.compile(r"\bnon[-\s]?disclosure\s+agreement\b", re.I),
        re.compile(r"\bmutual\s+nda\b", re.I),
        re.compile(r"\bmnda\b", re.I),
        re.compile(r"\bconfidentiality\s+agreement\b", re.I),
        re.compile(r"\bnda\b", re.I),
    )),
)
_DEFAULT_TYPE = "nda"
_TITLE_REGION_CHARS = 200
_TITLE_WEIGHT = 100


def detect_contract_type(text: str) -> tuple[str, bool]:
    """Detect contract type. Returns (type, was_ambiguous). Title region dominates;
    whole-document counts break ties. Defaults to NDA when nothing matches."""
    title = text[:_TITLE_REGION_CHARS]
    scores: dict[str, int] = {}
    for ctype, patterns in _TYPE_PATTERNS:
        title_hits = sum(len(p.findall(title)) for p in patterns)
        body_hits = sum(len(p.findall(text)) for p in patterns)
        scores[ctype] = _TITLE_WEIGHT * title_hits + body_hits
    best_type = max(scores, key=lambda t: scores[t])
    if scores[best_type] == 0:
        return _DEFAULT_TYPE, True
    return best_type, False


def load_playbook_bundle(contract_type: str) -> str:
    """The assembled per-type playbook bundle (role -> ... -> No-Signature Gate)."""
    return load_bundle(_PLAYBOOK_DIR, contract_type)


def attach_parent_msa(text: str, client_id: str, max_chars: int) -> tuple[str, str] | None:
    """Return (title, possibly-truncated MSA text) for the governing MSA, or None.

    `text` is accepted for a future party-name match; today it selects the single
    MSA on file for the client. Returns None when no MSA / no client_id.
    """
    parent = get_parent_msa(client_id)
    if not parent:
        return None
    title, msa_text = parent
    if len(msa_text) > max_chars:
        logger.warning("[grounding] MSA %r is %d chars — truncating to %d",
                       title, len(msa_text), max_chars)
        msa_text = msa_text[:max_chars] + f"\n\n[MSA truncated to {max_chars} chars for review]"
    return title, msa_text


_PREFERENCES_DIRECTIVE = (
    "The following are this attorney's standing working preferences. Apply them to "
    "emphasis, tone, and what to surface — but they do NOT override the playbook, "
    "firm policy, or any risk rating. When a preference conflicts with the playbook, "
    "the playbook wins.\n\n--- ATTORNEY PREFERENCES (USER.md) ---\n"
)


def load_attorney_preferences_block(attorney_id: str, base_dir: str, max_chars: int) -> str:
    """Formatted preferences system block for `attorney_id`, or '' when empty or on
    ANY error (logged). Pure (no settings) so it unit-tests with a tmp dir. The
    single assembly point both chat and review call — keeps the surfaces aligned.
    Preferences failure must never break a turn."""
    try:
        md = load_preferences(base_dir, attorney_id)
    except Exception as e:
        logger.warning("[grounding] preferences load failed for %r: %s", attorney_id, e)
        return ""
    md = md.strip()
    if not md:
        return ""
    if len(md) > max_chars:
        md = md[:max_chars] + f"\n\n[preferences truncated to {max_chars} chars]"
    return _PREFERENCES_DIRECTIVE + md + "\n--- END ATTORNEY PREFERENCES ---"


def preferences_block_for_state(state: dict) -> str:
    """Settings-gated, state-aware wrapper over load_attorney_preferences_block.
    Returns '' when disabled or when state has no user_id. Used by both the chat
    (skills/legal_research) and review (skills/contract_review) paths."""
    settings = get_settings()
    if not settings.preferences_enabled:
        return ""
    attorney_id = (state.get("user_id") or "").strip()
    if not attorney_id:
        return ""
    return load_attorney_preferences_block(
        attorney_id, settings.preferences_dir, settings.preferences_max_chars
    )
