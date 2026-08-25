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

WHAT THE GATE DOES NOT CATCH. Sentence segmentation of natural language is not decidable
by a deterministic rule, so one truncation shape survives: a quote ending at the period of
an abbreviation that is NOT in _NON_TERMINAL_ABBREVIATIONS, where the next word is
capitalised — "…from Acme Ltd." or "…, Esq." Titles and citation markers are listed and
closed; company suffixes deliberately are not, because they commonly do end a sentence
here and listing them cost three legitimate quotes to close one fabrication shape. The
residual is bounded by three things outside this module: the prompt asks for a sentence
from its first word through its ending punctuation, every quote is labelled "said earlier"
and ranked below live grounding, and the raw rows are never deleted, so any segment can be
audited against exactly the rows it cites.
"""
from __future__ import annotations

import logging
import re

from config import get_settings
from memory.conversation_store import load_rows_after
from memory.conversation_summary import append_segment, latest_to_id
from observability.tracing import traced_invoke
from skills.legal_research.edit_parsing import _sanitize_history
from skills.legal_research.legal_research import _build_llm
from skills.legal_research.prompts import _COMPACTION_SYSTEM

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

# A quote must be a COMPLETE sentence of the row it cites — not just a substring of
# it. Containment alone is not enough, and the gap is not theoretical: "accept 12
# months" is a genuine contiguous substring of "We will not accept 12 months.", and
# "We will accept 12 months" is a genuine prefix of "We will accept 12 months only if
# the cap is raised." Both pass a pure substring check while asserting the OPPOSITE of
# what the row says — a fabrication assembled entirely from real characters, which is
# exactly what this gate exists to make impossible. Requiring BOTH ends of the quote
# to land on sentence boundaries makes that class unconstructible: a negation or a
# condition either falls inside the quote or belongs to a different sentence.
#
# Sentence terminators ONLY — deliberately not ';' or ':'. Those SUBORDINATE what sits
# on the other side of them (a condition, a hedge, a qualification) where a full stop
# SEPARATES two independent thoughts, so admitting them reopens the very hole this
# check closes: "We will accept 12 months" is clause-aligned inside "We will accept 12
# months: only if the cap is raised." The cost is that a semicolon-joined clause can no
# longer be quoted on its own, which is the safe direction to err.
#
# A terminator also has to LOOK like a sentence end: followed by whitespace and then a
# capital, or ending the message. Without that, the period in "12.5 months" or "Acme
# Inc." serves as a false boundary and the same truncation walks straight through.
_CLAUSE_END = ".!?"
# Characters that can sit between a sentence terminator and the first real character of
# the next sentence: quote marks, brackets, and markdown emphasis. Skipping them is what
# lets "The cap is Green. (Section 12.5 is relevant.)" and "**Green** under the playbook"
# be quoted at all. It does NOT weaken the boundary rule — the requirement that
# whitespace follow the terminator is untouched, so "12.5" still cannot be split.
_EDGE_CHARS = "\"'([*_#>-"

# Abbreviations that are never the last word of a sentence — they exist to introduce a
# name, so the capital that follows them is a person or a case, not a new statement.
# Without this, "…of Smith v. Jones only if the cap is raised." lets a quote stop at
# "Smith v" and drop the condition entirely.
#
# Deliberately EXCLUDES company suffixes (Inc, Ltd, Corp, Co, St) and etc/cf. Those were
# measured and rejected: they commonly DO end a sentence in this domain ("We are dealing
# with Acme Inc. The cap is Green."), and listing them cost three legitimate quotes to
# close one extra fabrication shape. Matched case-sensitively, so the ordinary word "no"
# is unaffected by the entry for "No".
_NON_TERMINAL_ABBREVIATIONS = frozenset({
    "Mr", "Mrs", "Ms", "Dr", "Prof", "Jr", "Sr", "No", "v", "vs", "al", "cf",
})

_SEGMENT_HEADER = (
    "--- EARLIER IN THIS CONVERSATION ({count} earlier messages, condensed) ---\n"
    "This is recalled discussion, not a current finding. The attached document,\n"
    "the prior review and the playbook above take precedence over anything here."
)
_SEGMENT_FOOTER = "--- END EARLIER IN THIS CONVERSATION ---"


def _norm_shape(text: str) -> str:
    """Fold the differences that are not differences: curly quotes, nbsp, en/em dashes,
    runs of whitespace. Applied to BOTH sides of every comparison, so a quote and its
    source row are measured on equal terms.

    Case is deliberately PRESERVED here and folded only at the point of comparison.
    A capital letter after a full stop is the strongest available signal that a period
    genuinely ends a sentence rather than sitting inside "12.5" or "Acme Inc." —
    casefolding before that check is what let a decimal point serve as a false sentence
    boundary.
    """
    for src, dst in _NORMALISE.items():
        text = text.replace(src, dst)
    return " ".join(text.split())


def _token_before(row_text: str, i: int) -> str:
    """The whitespace-delimited token ending just before index `i`, without leading
    punctuation — "(Mr" must be recognised as "Mr", or a bracket defeats the guard."""
    j = i - 1
    while j >= 0 and not row_text[j].isspace():
        j -= 1
    return row_text[j + 1:i].lstrip('("\'-[')


def _sentence_end_indices(row_text: str) -> set[int]:
    """Indices of terminators in `row_text` that genuinely end a sentence.

    A terminator qualifies when it ends the string, or when whitespace follows it and
    the next visible character is a capital. Everything else is a period doing another
    job — the decimal point in "12.5 months", the abbreviation dot in "Acme Inc." —
    and treating those as sentence ends is what let a quote be truncated mid-statement
    while still appearing to end on a boundary.

    Erring toward NOT finding a boundary is the safe direction: it costs a legitimate
    quote, where the opposite costs a fabricated one.

    A terminator also does not count when the word before it is an abbreviation that
    never ends a sentence — "Mr.", "v.", "No." — because the capital that follows is a
    name rather than a new statement.
    """
    ends: set[int] = set()
    for i, ch in enumerate(row_text):
        if ch not in _CLAUSE_END:
            continue
        if i + 1 == len(row_text):
            ends.add(i)
            continue
        if not row_text[i + 1].isspace():
            continue
        j = i + 1
        while j < len(row_text) and (row_text[j].isspace() or row_text[j] in _EDGE_CHARS):
            j += 1
        if j < len(row_text) and not (row_text[j].isupper() or row_text[j].isdigit()):
            continue
        if _token_before(row_text, i) in _NON_TERMINAL_ABBREVIATIONS:
            continue
        ends.add(i)
    return ends


def _quotes_a_whole_sentence(row_text: str, quote: str) -> bool:
    """True when `quote` appears in `row_text` as a complete sentence.

    Both arguments must be shape-normalised with case intact. The match must begin at a
    sentence start (the row's start, or the first visible character after a genuine
    sentence end) and finish at the row's end or on a genuine sentence terminator —
    whether or not the quote includes that terminator. Every occurrence is tried, so a
    phrase appearing twice is accepted if either position aligns.

    A message with no terminal punctuation is therefore quotable only in full. That is
    restrictive and intended: with no boundaries to trust, any trim could be dropping a
    qualification.
    """
    ends = _sentence_end_indices(row_text)
    starts = {0}
    for i in sorted(ends):
        j = i + 1
        while j < len(row_text) and (row_text[j].isspace() or row_text[j] in _EDGE_CHARS):
            j += 1
        if j < len(row_text):
            starts.add(j)
    # re.finditer on the ORIGINAL string, not a casefolded copy: casefold is not
    # length-preserving (ß->ss, the ﬁ/ﬂ ligatures a PDF paste carries, İ), so matching on
    # a folded string while `starts`/`ends` index the unfolded one shifts every position
    # after such a character. The failure is silent over-rejection that never recovers —
    # both attempts fail, the range never advances, and compaction stays broken for that
    # document.
    for m in re.finditer(re.escape(quote), row_text, re.IGNORECASE):
        pos, end = m.start(), m.end()
        if pos in starts and (
            end == len(row_text)
            # Only a FULL STOP may be dropped. Allowing any terminator here let a quote
            # stop before a "?" and turn a question into a decision: "We will accept 12
            # months?" quoted as "We will accept 12 months". A quote may still INCLUDE
            # its "?" or "!" — that is the `end - 1` branch below.
            or (end in ends and row_text[end] == ".")
            or (end - 1) in ends
        ):
            return True
    return False


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

    Four checks per quote, all deterministic and zero-LLM:
      1. the cited id falls inside the segment's range;
      2. that row is present in the condensed transcript;
      3. the speaker label matches the row's role;
      4. the quoted text appears in that row after normalisation, AND does so as a
         complete sentence rather than a fragment chopped out of one.

    ANY failing quote invalidates the ENTIRE segment. A summary is legal recall;
    one invented line in it is worse than no summary at all, and there is no
    principled way to keep the rest of a block that demonstrably fabricated.

    What check 4 guarantees is that a quote is a complete sentence by every signal
    available without semantics: it starts where a sentence starts, ends where one ends,
    and no mid-token period counts as either. What it cannot guarantee is that an
    abbreviation followed by a capitalised name is not a sentence break — see the module
    docstring.
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
        nrow, nquote = _norm_shape(row["content"]), _norm_shape(q["text"])
        if nquote.casefold() not in nrow.casefold():
            return f"quote for row #{rid} does not appear in that message"
        if not _quotes_a_whole_sentence(nrow, nquote):
            return (
                f"quote for row #{rid} is not a complete sentence of that message"
            )
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


def select_compactable_rows(document_id: str, attorney_id: str) -> tuple[int, int, list[dict]]:
    """(from_id, to_id, sanitised rows) for the next segment, or (0, 0, []).

    Starts after the highest already-condensed row, so a second compaction picks
    up exactly where the first stopped: ranges never overlap and never gap.
    Leaves the most recent compaction_keep_recent_messages messages verbatim —
    recent nuance should be read, not quoted.

    from_id/to_id come from the ORIGINAL selection, before sanitising. That is
    deliberate: the range must cover every row it consumed, including rows
    _sanitize_history dropped as pure machinery. A quote citing one of those
    dropped rows then fails validation, which is the correct outcome — nothing
    can vouch for it. _sanitize_history strips fenced blocks from ASSISTANT rows
    only — user rows are byte-identical by design — so the guarantee that a
    fence cannot be quoted rests additionally on quotes being single-line and
    fence lines being skipped in parse_quote_lines, not on sanitisation alone.
    """
    boundary = latest_to_id(document_id, attorney_id)
    available = load_rows_after(
        document_id, attorney_id, boundary, _MAX_ROWS_PER_COMPACTION
    )
    keep = get_settings().compaction_keep_recent_messages
    if len(available) <= keep:
        return 0, 0, []
    selected = available[: len(available) - keep]
    from_id, to_id = selected[0]["id"], selected[-1]["id"]
    return from_id, to_id, _sanitize_history(selected)


def _render_transcript(rows: list[dict]) -> str:
    return "\n\n".join(
        f"[#{r['id']} {'attorney' if r['role'] == 'user' else 'assistant'}]\n{r['content']}"
        for r in rows
    )


def _generate_quote_lines(rows: list[dict], max_quotes: int, correction: str = "") -> str:
    """One LLM call: numbered transcript in, quote lines out.

    Isolated behind this seam so the flow tests replace it without a live model.
    Reuses the doc-chat LLM (temperature 0, num_predict 2048) — a capped segment
    is roughly 500 tokens, comfortably inside that.
    """
    user_message = _render_transcript(rows)
    if correction:
        # The model is called at temperature 0, so a second identical request returns an
        # identical rejection. Telling it what was wrong is the only thing that makes the
        # retry mean anything. Format correction, not legal coaching.
        user_message += (
            f"\n\n--- YOUR PREVIOUS ATTEMPT WAS REJECTED ---\n{correction}\n"
            "Copy each quote as a COMPLETE SENTENCE, from its first word through its "
            "ending punctuation, exactly as it appears in the message it cites."
        )
    response = traced_invoke(
        _build_llm(),
        [
            {"role": "system", "content": _COMPACTION_SYSTEM.format(max_quotes=max_quotes)},
            {"role": "user", "content": user_message},
        ],
        name="conversation_compaction",
    )
    return response.content if hasattr(response, "content") else str(response)


def compact_conversation(document_id: str, attorney_id: str) -> dict:
    """Condense the oldest un-condensed stretch of this conversation.

    Returns a result dict; never raises for an ordinary failure. Distinguishes
    two non-success cases on purpose:
      - reason  — nothing to condense. Not a failure; the caller answers 200.
      - error   — the gate rejected two attempts, or the write failed. LOUD:
                  the attorney clicked and was told it happened, so a silent
                  failure would be a lie (the same split as save_review vs
                  append_turn).
    A failure changes nothing: no segment row, no deleted turns.
    """
    empty = {
        "compacted": False, "from_id": 0, "to_id": 0, "messages": 0,
        "quotes": 0, "segment_id": 0, "reason": "", "error": "",
    }
    from_id, to_id, rows = select_compactable_rows(document_id, attorney_id)
    if not rows:
        return {**empty, "reason": "nothing earlier to condense yet"}

    settings = get_settings()
    max_quotes = settings.compaction_max_quotes
    last_error = ""
    # Distinguishes a connectivity failure (the model was unreachable) from a
    # verification failure (the model answered but the gate rejected it), so the
    # two attempts raising doesn't get reported as a fabrication-sounding "could
    # not be verified" when the real story is an Ollama outage.
    last_was_transport_failure = False
    for attempt in (1, 2):
        try:
            body = _generate_quote_lines(rows, max_quotes, last_error if attempt > 1 else "")
        except Exception as e:
            last_error = f"the model could not be reached ({e.__class__.__name__})"
            last_was_transport_failure = True
            logger.error("[compaction] generation failed on attempt %d: %s", attempt, e)
            continue
        last_was_transport_failure = False
        last_error = validate_segment(body, rows, from_id, to_id)
        if last_error:
            logger.warning(
                "[compaction] attempt %d rejected by the gate: %s", attempt, last_error
            )
            continue
        quotes, _ = parse_quote_lines(body)
        # Over-production is not fabrication: every line here already passed the
        # gate, so trim to the cap rather than burn a retry on a valid segment.
        if len(quotes) > max_quotes:
            logger.info(
                "[compaction] %d quotes returned, capping at %d", len(quotes), max_quotes
            )
            quotes = quotes[:max_quotes]
        # Chronological, not the model's emission order: a summary that lists a
        # superseded position after the one that replaced it misrepresents the
        # sequence, and row ids make the correct order free.
        quotes = sorted(quotes, key=lambda q: q["row_id"])
        content = render_segment(quotes, message_count=len(rows))
        try:
            segment_id = append_segment(document_id, attorney_id, from_id, to_id, content)
        except Exception as e:
            logger.error("[compaction] segment write failed: %s", e)
            return {**empty, "error": f"the condensed segment could not be saved ({e.__class__.__name__})"}
        logger.info(
            "[compaction] condensed rows %d-%d (%d messages) into %d quotes",
            from_id, to_id, len(rows), len(quotes),
        )
        return {
            "compacted": True, "from_id": from_id, "to_id": to_id,
            "messages": len(rows), "quotes": len(quotes),
            "segment_id": segment_id, "reason": "", "error": "",
        }

    if last_was_transport_failure:
        return {**empty, "error": last_error}
    return {**empty, "error": f"the condensed summary could not be verified: {last_error}"}
