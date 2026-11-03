# rag/tools/search_books.py
"""search_books: the research agent's search of the reference books, and what its results mark as retrieved.

Both book tools (this one and lookup_book) print passages in one format (`format_chunks`, spec D7 with
ruling 20). Each block names its passage with a `ref_id:` marker, which is what the model cites and what
`retrieved_book_refs` reads back: an id counts as retrieved only after that marker, in a book tool's
result. Ids printed without the marker (a chunk's history notes, the rest of a long lookup) were not
shown, so they do not count. No page and no edition is ever printed, so the model has none to copy:
those come from the stored payload when the answer is rendered (D8).

Neither tool takes a `client_id` or a collection: rag/book_search.py reads only the book collection.
"""
import logging
from collections.abc import Iterable, Sequence

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool

from ingest.books.models import BookChunk
from ingest.books.ref_ids import REF_MARKER_RE
from observability.degradations import BOOK_SEARCH_FAILED
from observability.spans import record_degradation, set_span_attributes, traced
from rag.book_search import search_book_chunks

logger = logging.getLogger(__name__)

BOOK_TOOL_NAMES = frozenset({"search_books", "lookup_book"})

_INDENT = "    "


def format_chunks(chunks: Sequence[BookChunk], start: int = 1) -> str:
    """One block per chunk, numbered from `start`, separated by a blank line:

        [1] ref_id: <ref_id>
            <label> · <kind> · <source_type>
            <heading path>                       (left out when the chunk has none)
            <body, one line per line>
            history notes: <ids>                 (only when there are any; ids only)
            effective-date note: <ids>           (only when there are any; ids only)
    """
    blocks = []
    for number, chunk in enumerate(chunks, start):
        lines = [f"[{number}] ref_id: {chunk.ref_id}", f"{chunk.label} · {chunk.kind} · {chunk.source_type}"]
        if chunk.heading:
            lines.append(chunk.heading)
        lines.extend(chunk.body.splitlines())
        if chunk.history_notes:
            lines.append(f"history notes: {', '.join(chunk.history_notes)}")
        if chunk.effective_date_notes:
            lines.append(f"effective-date note: {', '.join(chunk.effective_date_notes)}")
        blocks.append("\n".join([lines[0], *(_INDENT + line for line in lines[1:])]))
    return "\n\n".join(blocks)


def marked_ref_ids(text: str) -> list[str]:
    """The ids a book tool's output marks as retrieved, once each, in order."""
    return list(dict.fromkeys(REF_MARKER_RE.findall(text)))


def unavailable(exc: BaseException) -> str:
    """The text a book tool returns when the book cannot be read, after recording it (D13: silent, the
    agent may answer without the book). It names no id, so nothing counts as retrieved."""
    name = exc.__class__.__name__
    logger.warning("[book] the book could not be read: %s: %s", name, exc)
    record_degradation(BOOK_SEARCH_FAILED, announced=False, detail=name)
    return f"Book search is unavailable right now ({name}). Answer without the book and say so."


def retrieved_book_refs(messages: Iterable) -> set[str]:
    """The ref_ids the book tools returned in this run.

    Reads the results of the book tools only, and only after a `ref_id:` marker, as whole grammar ids
    (the rule `retrieved_doc_ids` follows for doc_ids). The agent's own messages are not evidence of
    retrieval, nor is another tool's text, nor a failed call: ToolNode turns an exception escaping a
    tool into an error message whose text can echo the model's own arguments.
    """
    ids: set[str] = set()
    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.name in BOOK_TOOL_NAMES and msg.status != "error":
            ids.update(REF_MARKER_RE.findall(str(msg.content)))
    return ids


@traced("tool.search_books")
def run_search_books(query: str, include_history_notes: bool = False) -> str:
    set_span_attributes(**{"book.query": query, "book.include_history_notes": include_history_notes})
    try:
        hits = search_book_chunks(query, include_history_notes=include_history_notes)
    except Exception as exc:
        return unavailable(exc)
    if not hits:
        set_span_attributes(**{"book.ref_ids": []})
        return "No passage in the book matched this query."
    out = format_chunks([hit.chunk for hit in hits])
    set_span_attributes(**{"book.ref_ids": marked_ref_ids(out)})
    return out


@tool
def search_books(query: str, include_history_notes: bool = False) -> str:
    """Search the reference book Circular 92: Title 17 U.S.C. (copyright) and related laws, Dec. 2025 edition.

    Use it for questions on U.S. copyright law. Returns the best-matching passages, each headed by its id;
    cite a passage as `ref_id: <id>`, with the id exactly as printed here. Amendment-history notes are
    left out unless include_history_notes is true.

    Args:
        query: What to look for, in words.
        include_history_notes: Also search the history notes (amendment history, not statutory text).
    """
    return run_search_books(query, include_history_notes)
