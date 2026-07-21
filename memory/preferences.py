"""Per-attorney preference memory — a plain `USER.md` file per attorney.

Stage 1 of the self-improving agent harness. The attorney OWNS the file (edits it
in the Word Preferences tab); the agent only READS it as grounding and SUGGESTS
additions the attorney commits. Because the human is the only writer, a raw
markdown file is safe — no concurrent agent writes, no LLM-regeneration drift.

Mirrors memory/review_store.py / memory/conversation_store.py: plain IO, base dir
passed per call. Storage only — prompt assembly lives in skills/grounding.py.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_FILENAME = "USER.md"
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_WRITE_CHARS = 20000  # hard ceiling on a stored USER.md (abuse guard)


class PreferenceTooLargeError(Exception):
    """A save exceeded _MAX_WRITE_CHARS."""


def _safe_attorney_id(attorney_id: str) -> str:
    """Return attorney_id if it is a safe single path segment, else raise.

    attorney_id becomes a directory name; allow only [A-Za-z0-9_-] so it can
    never escape base_dir (no '..', no '/', no spaces). It is a UUID / oid in
    practice.
    """
    if not attorney_id or not _SAFE_ID_RE.fullmatch(attorney_id):
        raise ValueError(f"unsafe attorney_id: {attorney_id!r}")
    return attorney_id


def _user_md_path(base_dir: str, attorney_id: str) -> Path:
    return Path(base_dir) / _safe_attorney_id(attorney_id) / _FILENAME


def load_preferences(base_dir: str, attorney_id: str) -> str:
    """The attorney's USER.md contents, or '' when absent. Never raises on a
    missing file; raises ValueError only on an unsafe attorney_id."""
    path = _user_md_path(base_dir, attorney_id)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def save_preferences(base_dir: str, attorney_id: str, markdown: str) -> None:
    """Replace the attorney's USER.md (last-write-wins); creates the dir. Raises
    PreferenceTooLargeError over the hard cap, ValueError on an unsafe id."""
    if len(markdown) > _MAX_WRITE_CHARS:
        raise PreferenceTooLargeError(
            f"preferences {len(markdown)} chars exceed limit {_MAX_WRITE_CHARS}"
        )
    path = _user_md_path(base_dir, attorney_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    logger.info("Preferences saved: attorney_id=%s (%d chars)", attorney_id, len(markdown))
