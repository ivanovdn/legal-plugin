# skills/legal_research/compaction.py
"""Condense earlier conversation into validated verbatim quotes.

Compaction exists to protect the CONTRACT, not to make things fit. History is
compressible; the document, the playbook and the prior review are not. On a
grounded MSA turn the fixed content is 131,446 chars of a 150,000-char budget,
so history's entire allowance is 18,554 chars — and those chars are the
difference between the model reading the signature blocks and not.

The format is EXTRACTIVE — short verbatim quotes carrying the
conversation_store row id they came from — because the fabrication risk in
summarisation comes from abstraction, not from which side spoke. A paraphrased
legal conclusion drifts; a quoted one cannot. And because every line cites a
row, fabrication becomes REJECTABLE rather than merely detectable: see
validate_segment. A narrative summary would leave it undetectable by
construction.

IMPORT DIRECTION IS ONE-WAY. This module may import from legal_research.py;
context.py must never import this module, or the package cycles.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# One compaction pass reads at most this many rows. Not a config knob: it is a
# safety bound on a single read, not a tuning dial, and it composes correctly
# with the design — a longer backlog simply takes a second compaction, which
# starts after the first segment's to_id.
_MAX_ROWS_PER_COMPACTION = 200

_FENCE_LINE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*$")
_QUOTE_RE = re.compile(
    r'^\[#(\d+)\s+(attorney|assistant)(?:,\s*said\s+earlier)?\]\s*(.+)$'
)
# Straight and curly, plus the guillemets some models reach for.
_QUOTE_CHARS = "\"'“”‘’«»"

_NORMALISE = {
    "“": '"', "”": '"', "‘": "'", "’": "'",
    " ": " ", "–": "-", "—": "-",
}

_SEGMENT_HEADER = (
    "--- EARLIER IN THIS CONVERSATION ({count} earlier messages, condensed) ---\n"
    "This is recalled discussion, not a current finding. The attached document,\n"
    "the prior review and the playbook above take precedence over anything here."
)
_SEGMENT_FOOTER = "--- END EARLIER IN THIS CONVERSATION ---"


def _norm(text: str) -> str:
    """Fold the differences that are not differences: curly quotes, nbsp, en/em
    dashes, runs of whitespace, case. Applied to BOTH sides of the containment
    check, so a quote and its source row are compared on equal terms."""
    for src, dst in _NORMALISE.items():
        text = text.replace(src, dst)
    return " ".join(text.split()).casefold()


def parse_quote_lines(body: str) -> tuple[list[dict], str]:
    """Parse the model's output into quotes. Returns (quotes, error).

    error is "" only when at least one quote parsed AND every non-empty,
    non-fence line was a quote. Prose mixed in with the quotes is rejected
    rather than dropped: an unparsed line is an unvalidated line, and the whole
    point of the format is that every line is checkable against a row.
    """
    quotes: list[dict] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or _FENCE_LINE_RE.match(raw_line):
            continue
        m = _QUOTE_RE.match(line)
        if not m:
            return [], f"output contained a line that is not a quote line: {line[:80]!r}"
        quotes.append({
            "row_id": int(m.group(1)),
            "speaker": m.group(2),
            "text": m.group(3).strip().strip(_QUOTE_CHARS).strip(),
        })
    if not quotes:
        return [], "no quote lines found in the model's output"
    return quotes, ""


def validate_segment(body: str, rows: list[dict], from_id: int, to_id: int) -> str:
    """Check every quote against the row it cites. Returns "" when valid.

    Three checks per quote, all deterministic and zero-LLM:
      1. the cited id falls inside the segment's range;
      2. that row is present in the condensed transcript;
      3. the speaker label matches the row's role, and the quoted text actually
         appears in that row after normalisation.

    ANY failing quote invalidates the ENTIRE segment. A summary is legal recall;
    one invented line in it is worse than no summary at all, and there is no
    principled way to keep the rest of a block that demonstrably fabricated.
    """
    quotes, err = parse_quote_lines(body)
    if err:
        return err
    by_id = {r["id"]: r for r in rows}
    for q in quotes:
        rid = q["row_id"]
        if not (from_id <= rid <= to_id):
            return f"quote cites row #{rid}, outside the condensed range {from_id}-{to_id}"
        row = by_id.get(rid)
        if row is None:
            return f"quote cites row #{rid}, which is not in the condensed transcript"
        expected = "attorney" if row["role"] == "user" else "assistant"
        if q["speaker"] != expected:
            return (
                f"quote for row #{rid} is labelled {q['speaker']}, "
                f"but that row is the {expected}"
            )
        if not q["text"]:
            return f"quote for row #{rid} is empty"
        if _norm(q["text"]) not in _norm(row["content"]):
            return f"quote for row #{rid} does not appear in that message"
    return ""


def render_segment(quotes: list[dict], message_count: int) -> str:
    """Wrap validated quotes in the injectable block.

    The header, the precedence note and the footer are written HERE, by code —
    never by the model. The model supplies quotes it can be held to; it does not
    get to write the disclaimer that says how much weight they carry.

    The header counts MESSAGES, not turns: a turn ordinal is not derivable from
    a row id without a per-row ordinal we do not store, and a wrong number in a
    header is exactly the class of small lie this feature exists to prevent.
    """
    lines = [
        f'[#{q["row_id"]} {q["speaker"]}'
        + (", said earlier" if q["speaker"] == "assistant" else "")
        + f'] "{q["text"]}"'
        for q in quotes
    ]
    return "\n".join([
        _SEGMENT_HEADER.format(count=message_count),
        "",
        *lines,
        _SEGMENT_FOOTER,
    ])
