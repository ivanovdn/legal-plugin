# skills/legal_research/review_recall.py
"""Make a stored review safe to re-inject into a later chat turn.

Three deterministic, conservative passes over the recalled markdown:
strip the Suggested Redlines section so chat does not re-propose fills; drop
placeholder findings the current document proves were filled afterwards; then
reconcile the No-Signature gate verdict against what survived.

Conservative by construction — it never drops a live blocker and never asserts
a signature go-ahead. _reconcile_gate_verdict is called from inside
_reconcile_review_with_doc, so the gate machinery is internal to this module.

Imports nothing from this project — markdown in, markdown out.
"""
import logging
import re
import unicodedata

logger = logging.getLogger(__name__)


def _strip_redlines_section(markdown: str) -> str:
    """Remove the 'Suggested Redlines / Fallbacks' section from review markdown.

    Finds the first heading (any level #–##) whose text contains
    "suggested redlines" (case-insensitive) and drops everything from that
    heading up to (but not including) the next heading of the same or higher
    level, or end-of-string if it is the last section. All other sections are
    preserved unchanged.

    Returns the markdown unchanged when no such section exists.
    """
    match = re.search(r"^(#{1,6})\s+.*suggested redlines.*$", markdown, re.IGNORECASE | re.MULTILINE)
    if not match:
        return markdown
    level = len(match.group(1))          # e.g. 1 for "#", 2 for "##"
    start = match.start()
    # Find the next heading at the same or higher level (fewer #s).
    next_heading = re.search(
        r"^#{1," + str(level) + r"}\s",
        markdown[match.end():],
        re.MULTILINE,
    )
    if next_heading:
        end = match.end() + next_heading.start()
    else:
        end = len(markdown)
    return markdown[:start] + markdown[end:]


# Placeholder reconciliation ---------------------------------------------------
# A backtick span qualifies as a placeholder quote if it contains any bracket
# token or underscore blank. A BARE bracket token is only treated as a
# placeholder when it is LABELED (starts with an uppercase letter, e.g.
# "[Legal Name]", "[Date]") — a generic blank like "[__]" is ambiguous across
# fields, so it is only ever considered inside its full backtick context.
_MARKER_IN_SPAN_RE = re.compile(r"\[[^\]]{0,40}\]|_{2,}")
_BARE_LABEL_RE = re.compile(r"\[[A-Z][^\]]{0,38}\]")
_SOURCE_TAG_RE = re.compile(r"\[\s*source\s*:", re.IGNORECASE)


def _normalize_for_match(text: str) -> str:
    """NFC + curly->straight quotes + nbsp/whitespace collapse, for tolerant
    substring matching between a review quote and the current document."""
    text = unicodedata.normalize("NFC", text)
    text = (
        text.replace("’", "'").replace("‘", "'")   # curly single quotes
        .replace("“", '"').replace("”", '"')       # curly double quotes
        .replace(" ", " ")                              # non-breaking space
    )
    return re.sub(r"\s+", " ", text).strip()


def _placeholder_candidates(review_markdown: str) -> list[str]:
    """Distinct placeholder strings quoted in the review: full backtick spans that
    carry a marker (e.g. `Signed by: [__]`) plus bare LABELED bracket tokens
    (e.g. [Legal Name]). Excludes generated-draft [Source: id] tags."""
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        s = raw.strip()
        if not s or s in seen or _SOURCE_TAG_RE.search(s):
            return
        seen.add(s)
        candidates.append(s)

    for m in re.finditer(r"`([^`]+)`", review_markdown):      # full field context
        span = m.group(1)
        # Only single-marker spans are droppable. A span bundling >=2 markers
        # (the MSA/SOW `Signed by: [__] / Title: [__] / ...` shape) can't be
        # reconciled atomically — a partial fill would make the whole span vanish
        # from the doc and wrongly drop a still-live blocker. Leave those intact.
        if len(_MARKER_IN_SPAN_RE.findall(span)) == 1:
            _add(span)
    for m in _BARE_LABEL_RE.finditer(review_markdown):        # bare labeled tokens
        _add(m.group(0))
    return candidates


# Gate-verdict reconciliation --------------------------------------------------
# After placeholder rows are dropped, the "No Signature Checklist Result" gate can
# still cite the now-filled placeholders. _surviving_blocker_count reads the
# structured Key Findings table (the source of truth for blockers, mirroring the
# Word parser's deriveBlockers) to decide whether any substantive blocker remains.
_KEY_FINDINGS_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*key\s+findings\s*$", re.IGNORECASE)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")   # any GFM heading (section boundary)
_BLOCKER_RATINGS = {"red", "missing context", "missing-context", "missing_context"}


def _surviving_blocker_count(review_markdown: str) -> int | None:
    """Count Key Findings rows rated Red or Missing Context. Returns None when the
    Key Findings table or its rating column can't be located/parsed — callers treat
    None as 'blockers may remain' (conservative: no gate downgrade). Runs on the
    row-reconciled markdown, so already-dropped placeholder rows never inflate it."""
    lines = review_markdown.splitlines()
    start = None
    for i, line in enumerate(lines):
        if _KEY_FINDINGS_HEADING_RE.match(line):
            start = i + 1
            break
    if start is None:
        return None
    rows: list[str] = []
    for line in lines[start:]:
        if _HEADING_RE.match(line):          # next section heading -> stop
            break
        if line.strip().startswith("|"):
            rows.append(line)
    if not rows:
        return None
    header = [c.strip().lower() for c in rows[0].strip().strip("|").split("|")]
    rating_idx = next((i for i, c in enumerate(header) if c in ("rating", "risk")), None)
    if rating_idx is None:
        return None
    count = 0
    for row in rows[1:]:
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) <= rating_idx:
            continue
        # Strip emphasis markup (**Red**, `Red`, _Red_) before matching, like the
        # Word parser's normalizeRisk. `.strip("_")` removes only SURROUNDING
        # underscores (italic wraps) — internal ones survive, so the
        # "missing_context" spelling in _BLOCKER_RATINGS is preserved (a naive
        # .replace("_","") would collapse it to "missingcontext").
        cell = cells[rating_idx].lower().replace("*", "").replace("`", "").strip().strip("_")
        if not cell or set(cell) <= {"-", ":", " "}:     # separator row (---, :---:)
            continue
        if cell in _BLOCKER_RATINGS:
            count += 1
    return count


_GATE_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*no[\s-]*signature\s+checklist", re.IGNORECASE)
_OVERALL_STATUS_RE = re.compile(r"^\s*(?:[-*]\s+)?(?:\*\*)?\s*overall status\s*:?", re.IGNORECASE)
_GATE_NEUTRAL_STATUS = (
    "Overall status: PENDING RE-REVIEW — the placeholder blockers recorded here were "
    "filled after this review and no other blockers remain; re-review to confirm "
    "signature readiness. (Do not treat as approved for signature.)"
)


def _reconcile_gate_verdict(review_markdown: str, dropped_tokens: list[str]) -> str:
    """Reconcile the No-Signature gate section after placeholder rows were dropped.

    Gate absent, or citing none of the dropped tokens -> unchanged. Otherwise insert
    a correction note naming the filled tokens (annotate), and — ONLY when zero Key
    Findings rows survive rated Red/Missing Context — rewrite 'Overall status:' to
    PENDING RE-REVIEW (neutralize). Never writes a signature go-ahead; never deletes
    the Blocking items text."""
    if not dropped_tokens:
        return review_markdown
    lines = review_markdown.splitlines()
    gate_start = next(
        (i for i, ln in enumerate(lines) if _GATE_HEADING_RE.match(ln)), None
    )
    if gate_start is None:
        return review_markdown
    gate_end = len(lines)
    for j in range(gate_start + 1, len(lines)):
        if _HEADING_RE.match(lines[j]):      # next section heading
            gate_end = j
            break
    norm_body = _normalize_for_match("\n".join(lines[gate_start + 1:gate_end]))
    cited = [t for t in dropped_tokens if _normalize_for_match(t) in norm_body]
    if not cited:
        return review_markdown

    neutralize = _surviving_blocker_count(review_markdown) == 0
    note = (
        "> **Reconciled:** placeholder blocker(s) cited in this gate — "
        + ", ".join("`" + t + "`" for t in cited)
        + " — were filled in the current document after this review; treat them as "
        "resolved. The current document governs the gate."
    )
    section = lines[gate_start:gate_end]
    if neutralize:
        for k in range(1, len(section)):
            if _OVERALL_STATUS_RE.match(section[k]):
                section[k] = _GATE_NEUTRAL_STATUS
                break
    section = [section[0], note] + section[1:]
    return "\n".join(lines[:gate_start] + section + lines[gate_end:])


def _reconcile_review_with_doc(review_markdown: str, doc_text: str) -> tuple[str, list[str]]:
    """Drop placeholder findings the current doc proves are filled.

    Returns (reconciled_markdown, dropped_tokens) — the tokens whose finding rows
    were actually removed (drives the reconciliation note; empty when nothing was
    dropped, in which case the input is returned unchanged). A candidate is 'filled'
    when its normalized form is no longer a substring of the normalized document. A
    line is dropped only when EVERY placeholder it references is filled (a line still
    holding a live placeholder is kept); section headings are never dropped. Only
    single-marker backtick spans are droppable — a span bundling several markers is
    left intact, so a partial fill can never drop a still-live blocker.
    """
    if not review_markdown or not doc_text:
        return review_markdown, []
    candidates = _placeholder_candidates(review_markdown)
    if not candidates:
        return review_markdown, []
    norm_doc = _normalize_for_match(doc_text)
    norm_cand = {c: _normalize_for_match(c) for c in candidates}
    filled = [c for c in candidates if norm_cand[c] not in norm_doc]
    if not filled:
        return review_markdown, []
    filled_set = set(filled)

    kept: list[str] = []
    dropped_tokens: list[str] = []
    seen_dropped: set[str] = set()
    in_gate = False
    for line in review_markdown.splitlines():
        if _HEADING_RE.match(line):        # never drop section headings
            in_gate = bool(_GATE_HEADING_RE.match(line))  # gate owned by _reconcile_gate_verdict
            kept.append(line)
            continue
        if in_gate:                              # gate reconciled separately; never row-drop it
            kept.append(line)
            continue
        norm_line = _normalize_for_match(line)
        refs = [c for c in candidates if norm_cand[c] in norm_line]
        # Most-specific-wins on a line: ignore a ref that is a proper substring of
        # another ref present on the same line, so a bare label ([Legal Name]) can't
        # out-vote its own filled span (`Landlord: [Legal Name]`) and keep a row that
        # was in fact filled. Without this, a label recurring across two contexts
        # (two parties / two signature blocks) would never reconcile.
        specific = [
            c for c in refs
            if not any(
                c != other
                and norm_cand[c] != norm_cand[other]
                and norm_cand[c] in norm_cand[other]
                for other in refs
            )
        ]
        if specific and all(c in filled_set for c in specific):
            for c in specific:                   # every placeholder here is filled -> drop
                if c not in seen_dropped:
                    seen_dropped.add(c)
                    dropped_tokens.append(c)
            continue
        kept.append(line)

    if not dropped_tokens:                        # filled candidates but no row removed
        return review_markdown, []                # -> no change, no over-claiming note

    body = _reconcile_gate_verdict("\n".join(kept), dropped_tokens)
    note = (
        f"> **Auto-reconciled:** {len(dropped_tokens)} placeholder(s) flagged in the "
        f"prior review were filled in the document afterward and have been removed from "
        f"the recalled findings: {', '.join('`' + c + '`' for c in dropped_tokens)}.\n\n"
    )
    return note + body, dropped_tokens
