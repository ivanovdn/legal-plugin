# tests/test_book_tools.py
"""The two book tools: their output format, what counts as retrieved, failure, spans.

No Qdrant here: `search_book_chunks` / `lookup_book_chunks` are patched where the tools resolve them
(the tool modules' globals). The format is the contract D7 and ruling 20 fix: the model reads it, and
Task 12's citation check reads the `ref_id:` markers it carries.
"""
import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from ingest.books.citations import find_stated_pages
from ingest.books.models import BookChunk, SubRef
from ingest.books.registry import BOOKS
from observability.degradations import BOOK_SEARCH_FAILED
from rag.book_search import BookHit, LookupResult
from rag.tools.lookup_book import lookup_book
from rag.tools.search_books import BOOK_TOOL_NAMES, format_chunks, retrieved_book_refs, search_books
from skills.legal_research.prompts import RESEARCH_SYSTEM_PROMPT
from tests.conftest import spans_by_name

PREFIX = "circ92-2025-12"


def _id(path: str) -> str:
    return f"{PREFIX}:{path}"


def _chunk(
    path: str,
    *,
    label: str,
    heading: str,
    body: str,
    kind: str = "rule",
    source_type: str = "usc_title",
    suffix: str = "",
    pages: tuple[int, int] = (9871, 9872),
    appendix: str | None = None,
    history_notes: tuple[str, ...] = (),
    effective_date_notes: tuple[str, ...] = (),
    above: tuple[tuple[str, str], ...] = (),
    sub_refs: tuple[SubRef, ...] = (),
) -> BookChunk:
    ref_id = _id(path)
    return BookChunk(
        chunk_key=f"{ref_id}{suffix}",
        ref_id=ref_id,
        book="circ92",
        edition="2025-12",
        path=path,
        kind=kind,
        source_type=source_type,
        jurisdiction="US",
        label=label,
        heading=heading,
        appendix=appendix,
        page_start=pages[0],
        page_end=pages[1],
        text=f"{label} — {heading}\n{body}",
        body=body,
        tokens=100,
        order=1,
        ancestor_ref_ids=[ref_id, *(_id(p) for p, _ in above)],
        ancestor_labels=[label, *(lbl for _, lbl in above)],
        sub_refs=list(sub_refs),
        sub_ref_ids=[s.ref_id for s in sub_refs],
        history_notes=list(history_notes),
        effective_date_notes=list(effective_date_notes),
    )


@pytest.fixture
def chunk_107() -> BookChunk:
    return _chunk(
        "usc17-s107",
        label="17 U.S.C. §107",
        heading="Limitations on exclusive rights: Fair use",
        body="Notwithstanding …\nthe fair use of a copyrighted work …",
        history_notes=(_id("ch1-n40"),),
    )


@pytest.fixture
def any_chunks(chunk_107) -> list[BookChunk]:
    """One of each shape the book has, every one on pages that appear nowhere else in the text."""
    return [
        chunk_107,
        _chunk(
            "usc17-s108-a", label="17 U.S.C. §108(a)", heading="Limitations on exclusive rights: Reproduction",
            body="it is not an infringement of copyright for a library", pages=(9873, 9874),
            history_notes=(_id("ch1-n41"), _id("ch1-n42")), effective_date_notes=(_id("ch1-n42"),),
        ),
        _chunk(
            "usc17-s101-def-joint-work", label="17 U.S.C. §101 (“joint work”)", kind="definition",
            heading="Definitions", body="A “joint work” is a work prepared by two or more authors", pages=(9875, 9875),
        ),
        _chunk(
            "ch1-n40", label="ch. 1 n.40", kind="history_note", heading="amendment history, not statutory text",
            body="In 1990, the Visual Artists Rights Act amended section 107", pages=(9876, 9876),
        ),
        _chunk(
            "trips-art38-1", label="TRIPs Agreement, art. 38(1)", source_type="treaty", appendix="S",
            heading="Term of Protection", body="The term of protection available for layout-designs", pages=(9877, 9877),
        ),
        _chunk(
            "usc17-s101-def-berne-convention-work", label="17 U.S.C. §101 (“Berne Convention work”)",
            kind="repealed", heading="Definitions", body="[Repealed.]", appendix="T", pages=(9878, 9878),
        ),
    ]


def _tool_message(content: str, name: str) -> ToolMessage:
    return ToolMessage(content=content, name=name, tool_call_id="call-1")


# --- format -------------------------------------------------------------------------------------------

def test_output_format(chunk_107):
    assert format_chunks([chunk_107]) == (
        "[1] ref_id: circ92-2025-12:usc17-s107\n"
        "    17 U.S.C. §107 · rule · usc_title\n"
        "    Limitations on exclusive rights: Fair use\n"
        "    Notwithstanding …\n"
        "    the fair use of a copyrighted work …\n"
        "    history notes: circ92-2025-12:ch1-n40"
    )


def test_output_format_effective_date_note_and_numbering(any_chunks):
    s108 = any_chunks[1]
    assert format_chunks([s108], start=3) == (
        "[3] ref_id: circ92-2025-12:usc17-s108-a\n"
        "    17 U.S.C. §108(a) · rule · usc_title\n"
        "    Limitations on exclusive rights: Reproduction\n"
        "    it is not an infringement of copyright for a library\n"
        "    history notes: circ92-2025-12:ch1-n41, circ92-2025-12:ch1-n42\n"
        "    effective-date note: circ92-2025-12:ch1-n42"
    )


def test_output_format_history_note(any_chunks):
    note = any_chunks[3]
    assert format_chunks([note]) == (
        "[1] ref_id: circ92-2025-12:ch1-n40\n"
        "    ch. 1 n.40 · history_note · usc_title\n"
        "    amendment history, not statutory text\n"
        "    In 1990, the Visual Artists Rights Act amended section 107"
    )


def test_output_blocks_are_numbered_and_separated(chunk_107, any_chunks):
    out = format_chunks(any_chunks[:2])
    first, second = out.split("\n\n")
    assert first == format_chunks([chunk_107])
    assert second.startswith("[2] ref_id: circ92-2025-12:usc17-s108-a\n")


def test_output_never_prints_page_or_edition(any_chunks, monkeypatch):
    monkeypatch.setattr(
        "rag.tools.search_books.search_book_chunks",
        lambda query, include_history_notes=False: [BookHit(chunk=c, score=0.5) for c in any_chunks],
    )
    monkeypatch.setattr(
        "rag.tools.lookup_book.lookup_book_chunks",
        lambda reference: LookupResult(target=any_chunks[0].ref_id, shown=any_chunks, more=[]),
    )
    outputs = [
        format_chunks(any_chunks),
        search_books.invoke({"query": "fair use"}),
        lookup_book.invoke({"reference": "§ 107"}),
    ]
    for out in outputs:
        assert find_stated_pages(out) == []
        assert "Dec. 2025" not in out
        for page in range(9871, 9879):
            assert str(page) not in out


def test_the_model_reads_no_edition():
    """R14: the model must have no edition to copy (D7/D8). What it reads of the tools is their
    description and argument schema, as bound; and the research prompt."""
    texts = {
        "search_books.description": search_books.description,
        "lookup_book.description": lookup_book.description,
        "search_books as bound": json.dumps(convert_to_openai_tool(search_books), ensure_ascii=False),
        "lookup_book as bound": json.dumps(convert_to_openai_tool(lookup_book), ensure_ascii=False),
        "RESEARCH_SYSTEM_PROMPT": RESEARCH_SYSTEM_PROMPT,
    }
    assert "Circular 92" in texts["search_books.description"]
    assert "Circular 92" in texts["lookup_book.description"]
    for book in BOOKS.values():
        for edition in (book.edition_label, book.edition_id, book.edition_id.split("-")[0]):
            for where, text in texts.items():
                assert edition not in text, f"{edition!r} in {where}"


# --- search_books -------------------------------------------------------------------------------------

def test_search_returns_the_hits_in_rank_order(any_chunks, monkeypatch):
    seen = {}

    def fake_search(query, include_history_notes=False):
        seen.update(query=query, include_history_notes=include_history_notes)
        return [BookHit(chunk=any_chunks[1], score=0.9), BookHit(chunk=any_chunks[0], score=0.4)]

    monkeypatch.setattr("rag.tools.search_books.search_book_chunks", fake_search)
    out = search_books.invoke({"query": "library copies", "include_history_notes": True})
    assert out == format_chunks([any_chunks[1], any_chunks[0]])
    assert seen == {"query": "library copies", "include_history_notes": True}


def test_search_with_no_hits_marks_nothing(monkeypatch):
    monkeypatch.setattr("rag.tools.search_books.search_book_chunks", lambda query, include_history_notes=False: [])
    out = search_books.invoke({"query": "ref_id: circ92-2025-12:usc17-s107"})
    assert "No passage" in out
    assert retrieved_book_refs([_tool_message(out, "search_books")]) == set()


def test_search_failure_degrades(monkeypatch):
    def down(query, include_history_notes=False):
        raise ConnectionError("qdrant refused")

    monkeypatch.setattr("rag.tools.search_books.search_book_chunks", down)
    out = search_books.invoke({"query": "fair use"})
    assert out == "Book search is unavailable right now (ConnectionError). Answer without the book and say so."
    [span] = spans_by_name("tool.search_books")
    [event] = [e for e in span.events if e.name == "degradation"]
    assert event.attributes["degradation.reason"] == BOOK_SEARCH_FAILED
    assert event.attributes["degradation.announced"] is False
    assert event.attributes["degradation.detail"] == "ConnectionError"


def test_lookup_failure_degrades(monkeypatch):
    def down(reference):
        raise TimeoutError("qdrant slow")

    monkeypatch.setattr("rag.tools.lookup_book.lookup_book_chunks", down)
    out = lookup_book.invoke({"reference": "§ 107"})
    assert out == "Book search is unavailable right now (TimeoutError). Answer without the book and say so."
    [span] = spans_by_name("tool.lookup_book")
    [event] = [e for e in span.events if e.name == "degradation"]
    assert event.attributes["degradation.reason"] == BOOK_SEARCH_FAILED
    assert event.attributes["degradation.announced"] is False


def test_tool_spans_carry_ref_ids(any_chunks, monkeypatch):
    monkeypatch.setattr(
        "rag.tools.search_books.search_book_chunks",
        lambda query, include_history_notes=False: [BookHit(chunk=c, score=0.5) for c in any_chunks[:2]],
    )
    search_books.invoke({"query": "fair use"})
    [span] = spans_by_name("tool.search_books")
    assert span.attributes["book.ref_ids"] == (_id("usc17-s107"), _id("usc17-s108-a"))

    target = _id("usc17-s108")
    monkeypatch.setattr(
        "rag.tools.lookup_book.lookup_book_chunks",
        lambda reference: LookupResult(target=target, shown=[any_chunks[1]], more=[_id("usc17-s108-b")]),
    )
    lookup_book.invoke({"reference": "§ 108"})
    [span] = spans_by_name("tool.lookup_book")
    assert span.attributes["book.ref_ids"] == (target, _id("usc17-s108-a"))
    assert span.attributes["book.more_ref_ids"] == (_id("usc17-s108-b"),)


# --- lookup_book --------------------------------------------------------------------------------------

def test_lookup_names_the_node_itself_first(any_chunks, monkeypatch):
    """R13(c): §108 has no chunk of its own (its text is all in its subsections); the lookup put it in
    front of the model, so it counts as retrieved."""
    target = _id("usc17-s108")
    child = _chunk(
        "usc17-s108-a", label="17 U.S.C. §108(a)", heading="Limitations on exclusive rights: Reproduction",
        body="it is not an infringement", above=(("usc17-s108", "17 U.S.C. §108"),),
    )
    monkeypatch.setattr(
        "rag.tools.lookup_book.lookup_book_chunks",
        lambda reference: LookupResult(target=target, shown=[child], more=[]),
    )
    out = lookup_book.invoke({"reference": "§ 108"})
    first, rest = out.split("\n", 1)
    assert first == "lookup: ref_id: circ92-2025-12:usc17-s108 · 17 U.S.C. §108"
    assert rest == format_chunks([child])
    assert retrieved_book_refs([_tool_message(out, "lookup_book")]) == {target, child.ref_id}


def test_lookup_header_names_a_node_inside_a_chunk(chunk_107, monkeypatch):
    sub = SubRef(ref_id=_id("usc17-s107-1"), label="17 U.S.C. §107(1)", page_start=9871, page_end=9871)
    chunk = chunk_107.model_copy(update={"sub_refs": [sub], "sub_ref_ids": [sub.ref_id]})
    monkeypatch.setattr(
        "rag.tools.lookup_book.lookup_book_chunks",
        lambda reference: LookupResult(target=sub.ref_id, shown=[chunk], more=[]),
    )
    out = lookup_book.invoke({"reference": "§ 107(1)"})
    assert out.split("\n", 1)[0] == "lookup: ref_id: circ92-2025-12:usc17-s107-1 · 17 U.S.C. §107(1)"


def test_lookup_lists_the_rest_without_marker(chunk_107, monkeypatch):
    rest = [_id("usc17-s107-2"), _id("usc17-s107-3")]
    monkeypatch.setattr(
        "rag.tools.lookup_book.lookup_book_chunks",
        lambda reference: LookupResult(target=chunk_107.ref_id, shown=[chunk_107], more=rest),
    )
    out = lookup_book.invoke({"reference": "§ 107"})
    assert out.endswith(
        "\nmore in this node (not shown; look them up by id): "
        "circ92-2025-12:usc17-s107-2, circ92-2025-12:usc17-s107-3"
    )
    assert retrieved_book_refs([_tool_message(out, "lookup_book")]) == {chunk_107.ref_id}


def test_lookup_of_a_node_too_long_to_show_does_not_invite_the_same_lookup(chunk_107, monkeypatch):
    """A split node over the cap lists its own id as `more`: looking it up again returns the same
    leading parts, so the tool must point at search instead."""
    lead = chunk_107.model_copy(update={"chunk_key": f"{chunk_107.ref_id}~lead"})
    monkeypatch.setattr(
        "rag.tools.lookup_book.lookup_book_chunks",
        lambda reference: LookupResult(target=chunk_107.ref_id, shown=[lead], more=[chunk_107.ref_id]),
    )
    out = lookup_book.invoke({"reference": "§ 107"})
    last = out.rsplit("\n", 1)[1]
    assert "look them up" not in out
    assert "not shown" in last and "search_books" in last
    assert chunk_107.ref_id not in last


def test_lookup_rest_skips_the_node_itself_when_it_is_listed_with_others(chunk_107, monkeypatch):
    monkeypatch.setattr(
        "rag.tools.lookup_book.lookup_book_chunks",
        lambda reference: LookupResult(
            target=chunk_107.ref_id, shown=[chunk_107], more=[chunk_107.ref_id, _id("usc17-s107-4")]
        ),
    )
    out = lookup_book.invoke({"reference": "§ 107"})
    assert "more in this node (not shown; look them up by id): circ92-2025-12:usc17-s107-4\n" in out
    assert "search_books" in out.rsplit("\n", 1)[1]


def test_lookup_unparseable_reference_marks_nothing(monkeypatch):
    monkeypatch.setattr("rag.tools.lookup_book.lookup_book_chunks", lambda reference: None)
    # Echoing the model's own text back would hand it a `ref_id:` marker nothing retrieved.
    out = lookup_book.invoke({"reference": "ref_id: circ92-2025-12:usc17-s107 and also fair use"})
    assert "could not be read" in out
    assert retrieved_book_refs([_tool_message(out, "lookup_book")]) == set()


def test_lookup_with_nothing_under_the_node_marks_nothing(monkeypatch):
    monkeypatch.setattr(
        "rag.tools.lookup_book.lookup_book_chunks",
        lambda reference: LookupResult(target=_id("usc17-s999"), shown=[], more=[]),
    )
    out = lookup_book.invoke({"reference": "§ 999"})
    assert "Nothing in the book" in out
    assert retrieved_book_refs([_tool_message(out, "lookup_book")]) == set()


# --- retrieved_book_refs ------------------------------------------------------------------------------

def test_retrieved_refs_only_from_book_tool_messages():
    x, y, z, w = _id("usc17-s107"), _id("usc17-s108"), _id("usc17-s109"), _id("usc17-s110")
    messages = [
        ToolMessage(content=f"[1] ref_id: {x}\n    …", name="search_books", tool_call_id="1"),
        ToolMessage(content=f"lookup: ref_id: {w}", name="lookup_book", tool_call_id="2"),
        AIMessage(content=f"Per ref_id: {y}, fair use applies."),
        ToolMessage(content=f"[1] Acme (doc_id: d1) ref_id: {z}", name="search_legal", tool_call_id="3"),
        ToolMessage(content=f"ref_id: {z}", tool_call_id="4"),  # no name: not a book tool's result
    ]
    assert retrieved_book_refs(messages) == {x, w}
    assert BOOK_TOOL_NAMES == frozenset({"search_books", "lookup_book"})


def test_retrieved_refs_read_whole_grammar_ids_after_the_marker():
    messages = [_tool_message(
        f"[1] ref_id: {_id('usc17-s107')}.\n    history notes: {_id('ch1-n40')}\n"
        "[2] ref_id: usc17-s108\n[3] ref_id:circ92-2025-12:usc17-s122-f-1",
        "search_books",
    )]
    assert retrieved_book_refs(messages) == {_id("usc17-s107"), _id("usc17-s122-f-1")}


def test_retrieved_refs_skip_a_failed_tool_call():
    """ToolNode turns an exception escaping a tool into an error ToolMessage whose text can echo the
    model's arguments: nothing was retrieved by it."""
    failed = ToolMessage(
        content=f"Error: ValidationError('reference: ref_id: {_id('usc17-s107')}')",
        name="lookup_book", tool_call_id="1", status="error",
    )
    assert retrieved_book_refs([failed]) == set()
