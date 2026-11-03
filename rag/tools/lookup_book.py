# rag/tools/lookup_book.py
"""lookup_book: the research agent's lookup of one passage of the reference books by citation or id.

The output starts with a line naming the looked-up node with its `ref_id:` marker (ruling R13(c)), so the
node counts as retrieved even when it has no chunk of its own (a section whose text is all in its
subsections): the lookup put all of it, or its first part, in front of the model. Then its chunks, in the
format search_books uses. Chunks past `BOOK_LOOKUP_MAX_TOKENS` are listed by id WITHOUT the marker,
since they were not shown.
"""
from langchain_core.tools import tool

from observability.spans import set_span_attributes, traced
from rag.book_search import LookupResult, lookup_book_chunks
from rag.tools.search_books import format_chunks, marked_ref_ids, unavailable

# The model's reference is never echoed back: text such as "ref_id: <id> and fair use" does not parse,
# and printing it would hand the model a marker for a passage nothing retrieved.
_UNPARSEABLE = (
    "That reference could not be read as a citation. Use an id search_books printed, or a citation "
    "such as \"§ 107\", \"17 U.S.C. §122(f)(1)\" or \"section 101 'joint work'\"."
)


def _target_label(result: LookupResult) -> str | None:
    """The looked-up node's label, from a shown chunk: its own or an ancestor's, or a node inside it."""
    for chunk in result.shown:
        if result.target in chunk.ancestor_ref_ids:
            return chunk.ancestor_labels[chunk.ancestor_ref_ids.index(result.target)]
        for sub in chunk.sub_refs:
            if sub.ref_id == result.target:
                return sub.label
    return None


def format_lookup(result: LookupResult) -> str:
    if not result.shown:
        return f"Nothing in the book is filed under {result.target}."
    label = _target_label(result)
    header = f"lookup: ref_id: {result.target}" + (f" · {label}" if label else "")
    lines = [header, format_chunks(result.shown)]
    others = [ref_id for ref_id in result.more if ref_id != result.target]
    if others:
        lines.append(f"more in this node (not shown; look them up by id): {', '.join(others)}")
    if result.target in result.more:
        # A node whose own text is longer than one lookup: looking its id up again returns the same
        # leading parts, so the rest is reachable only by search.
        lines.append(
            "the rest of this node's own text is not shown (too long for one lookup); "
            "use search_books with a narrower question to find the part you need"
        )
    return "\n".join(lines)


@traced("tool.lookup_book")
def run_lookup_book(reference: str) -> str:
    set_span_attributes(**{"book.reference": reference})
    try:
        result = lookup_book_chunks(reference)
    except Exception as exc:
        return unavailable(exc)
    if result is None:
        set_span_attributes(**{"book.ref_ids": []})
        return _UNPARSEABLE
    out = format_lookup(result)
    set_span_attributes(**{"book.ref_ids": marked_ref_ids(out), "book.more_ref_ids": result.more})
    return out


@tool
def lookup_book(reference: str) -> str:
    """Look up one passage of the reference book Circular 92: Title 17 U.S.C. (copyright) and related laws.

    Use it when you know the citation: a section such as "§ 107", "17 U.S.C. §122(f)(1)" or
    "section 101 'joint work'", or an id a search printed (a history note's id returns the note).
    Returns the passage's text in order; cite it as `ref_id: <id>`, with the id exactly as printed here.

    Args:
        reference: The citation or id to look up.
    """
    return run_lookup_book(reference)
