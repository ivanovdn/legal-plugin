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
from skills.base import _clean_for_prompt, load_bundle, load_chat_bundle
from config import get_settings
from memory.preferences import load_preferences
from observability.degradations import PREFERENCES_LOAD_FAILED
from observability.spans import note_turn, record_degradation

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
    """The assembled per-type playbook bundle (role -> ... -> No-Signature Gate),
    wrapped for a contract review."""
    return load_bundle(_PLAYBOOK_DIR, contract_type)


def load_chat_playbook(contract_type: str) -> str:
    """The same bundle wrapped for doc chat: the legal ceiling without the
    review's output form (skills/base.py::_CHAT_CEILING_PREFIX)."""
    return load_chat_bundle(_PLAYBOOK_DIR, contract_type)


# A SOW names its MSA in its opening, in a sentence such as "…is made pursuant to
# and subject to the Master Service Agreement dated [Month] [Date], [Year] (the
# "MSA")…" — the Trinetix template's own wording (SOW rule 1), at character 375
# of the template. Only the opening counts: pilot turn e9a92775's standalone SOW
# first says "this Master Service Agreement" at character 22,126, in a liability
# clause carried over from an MSA, and quoting that as "this SOW names an MSA" led
# gpt-oss to treat the SOW's own terms as an MSA's (replay, 2026-11-25). The full
# name is preferred; a bare "MSA" is the fallback.
_MSA_REFERENCE_PATTERNS = (
    re.compile(r"\bmaster\s+services?\s+agreement\b", re.I),
    re.compile(r"\bMSA\b"),
)
_MSA_REFERENCE_OPENING_CHARS = 2000
_MSA_REFERENCE_MAX_CHARS = 400


def msa_reference(text: str) -> str:
    """The sentence in the opening of `text` (its first
    _MSA_REFERENCE_OPENING_CHARS characters) that names an MSA,
    whitespace-collapsed and capped at _MSA_REFERENCE_MAX_CHARS; "" when the
    opening names none. A sentence ends at a full stop or a line break, so an
    abbreviation such as "Inc." can end it early; the quote is a pointer for the
    model, not evidence."""
    for pattern in _MSA_REFERENCE_PATTERNS:
        match = pattern.search(text, 0, _MSA_REFERENCE_OPENING_CHARS)
        if not match:
            continue
        start = max(text.rfind(".", 0, match.start()), text.rfind("\n", 0, match.start()),
                    text.rfind("\r", 0, match.start())) + 1
        ends = [i for i in (text.find(".", match.end()), text.find("\n", match.end()),
                            text.find("\r", match.end())) if i != -1]
        end = min(ends) + 1 if ends else len(text)
        sentence = " ".join(text[start:end].split())
        if len(sentence) > _MSA_REFERENCE_MAX_CHARS:
            sentence = sentence[:_MSA_REFERENCE_MAX_CHARS].rsplit(" ", 1)[0] + " …"
        return sentence
    return ""


# The parts of the MSA skill that state the firm's positions. Its other sections
# (mandatory behaviour, review workflow, No Signature Gate, required output,
# quality checklist) are the procedure for reviewing an MSA, which would compete
# with the SOW playbook a SOW turn is already following.
_MSA_POSITION_SECTIONS = ("Source position", "Clause review rules")


def _sections(markdown: str, names: tuple[str, ...]) -> str:
    """The named `## ` sections of a playbook file, in file order, each running to
    the next `## ` heading. Raises ValueError when one is missing, so a rebuilt
    playbook that renames a heading fails loudly instead of dropping the rules."""
    kept: list[list[str]] = []
    found: set[str] = set()
    current: list[str] | None = None
    for line in markdown.splitlines():
        if line.startswith("## "):
            name = line[3:].strip()
            current = [line] if name in names else None
            if current is not None:
                kept.append(current)
                found.add(name)
        elif current is not None:
            current.append(line)
    missing = [n for n in names if n not in found]
    if missing:
        raise ValueError(f"playbook section(s) not found: {missing}")
    return "\n\n".join("\n".join(section).strip() for section in kept)


def load_msa_positions() -> str:
    """The firm's MSA positions, for a SOW turn with no governing MSA to compare
    against: the MSA skill's source position and clause rules, then the MSA
    playbook matrix. Pilot turn e9a92775 (2026-11-03) asked which MSA terms a
    standalone SOW needed; with none of this in the prompt, the model wrote MSA
    terms from its own knowledge."""
    skill = _clean_for_prompt((_PLAYBOOK_DIR / "msa" / "SKILL.md").read_text(encoding="utf-8"))
    matrix = _clean_for_prompt(
        (_PLAYBOOK_DIR / "msa" / "playbook_matrix.md").read_text(encoding="utf-8")
    )
    return _sections(skill, _MSA_POSITION_SECTIONS) + "\n\n" + matrix.strip()


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
        record_degradation(PREFERENCES_LOAD_FAILED, announced=False, detail=e.__class__.__name__)
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
    block = load_attorney_preferences_block(
        attorney_id, settings.preferences_dir, settings.preferences_max_chars
    )
    note_turn(preferences_chars=len(block))
    return block
