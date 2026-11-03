"""D8's citation check and rendering: code, not the model, answers for a book citation.

`check_book_citations` resolves every cited ref_id once, in risk_assessor, into the per-turn
`book_citations`; the risk flags, the rendered response and the audit row all read that one list
(ruling 13). The page check reads the RAW answer, and only when the book is in play (ruling 12).

`fetch_nodes` is replaced by a fixed store, patched where the check resolves it:
rag.book_citations' globals.
"""
import logging

import pytest

from graph.nodes.output_formatter import output_formatter
from graph.nodes.risk_assessor import _check_citations, risk_assessor
from ingest.books.citations import find_stated_pages
from rag.book_citations import check_book_citations
from rag.book_search import NodeInfo

S106 = "circ92-2025-12:usc17-s106"
S106_1 = "circ92-2025-12:usc17-s106-1"  # inside §106's chunk
S107 = "circ92-2025-12:usc17-s107"
S107_1 = "circ92-2025-12:usc17-s107-1"  # inside §107's chunk
S108 = "circ92-2025-12:usc17-s108"
OTHER_EDITION = "circ92-2024-01:usc17-s107"

NOT_RETRIEVED = "Cites a reference that was not retrieved: {}"
UNVERIFIABLE = "Could not verify a cited reference: {}"
STATED_PAGE = "The answer states a page itself"
NO_CITATION_POSSIBLE = "No citation possible — no chunks retrieved"
NO_CITATION_FOUND = "No citation found — response does not reference any retrieved document"

CITE_107 = "17 U.S.C. §107 — Circular 92 (Dec. 2025), pp. 20–21"
CITE_107_1 = "17 U.S.C. §107(1) — Circular 92 (Dec. 2025), p. 21"

DOC_CHUNK = {"chunk_id": "c1", "doc_id": "d1", "doc_title": "Master Services Agreement", "text": "…"}


def _node(ref_id, label, *, pages, containers=None):
    return NodeInfo(
        ref_id=ref_id, label=label, kind="rule", source_type="usc_title", appendix=None,
        page_start=pages[0], page_end=pages[1], containers=containers or {ref_id},
    )


STORE = {
    S106: _node(S106, "17 U.S.C. §106", pages=(9, 10)),
    S106_1: _node(S106_1, "17 U.S.C. §106(1)", pages=(9, 9), containers={S106}),
    S107: _node(S107, "17 U.S.C. §107", pages=(20, 21)),
    S107_1: _node(S107_1, "17 U.S.C. §107(1)", pages=(21, 21), containers={S107}),
    S108: _node(S108, "17 U.S.C. §108", pages=(22, 30)),
}


@pytest.fixture
def store(monkeypatch):
    """fetch_nodes over STORE. Returns the list of calls, each the ids asked for."""
    calls: list[list[str]] = []

    def fake_fetch_nodes(ref_ids):
        calls.append(list(ref_ids))
        return {r: STORE[r] for r in ref_ids if r in STORE}

    monkeypatch.setattr("rag.book_citations.fetch_nodes", fake_fetch_nodes)
    return calls


@pytest.fixture
def qdrant_down(monkeypatch):
    def failing_fetch_nodes(ref_ids):
        raise RuntimeError("[Errno 111] Connection refused")

    monkeypatch.setattr("rag.book_citations.fetch_nodes", failing_fetch_nodes)


def _research(answer, *, retrieved=(), turn=(), chunks=(), uploaded=False):
    return {
        "task_type": "research",
        "llm_response": answer,
        "retrieved_chunks": list(chunks),
        "uploaded_docs": [{"text": "MASTER SERVICES AGREEMENT …"}] if uploaded else [],
        "book_refs_retrieved": list(retrieved),
        "book_refs_turn": list(turn),
    }


def _reasons(state):
    return [f["reason"] for f in state["risk_flags"]]


def _flag(reason):
    return {"reason": reason, "severity": "high"}


# --- validity -----------------------------------------------------------------------------------


def test_book_only_turn_with_valid_citations_passes(store):
    """No client chunks at all: before D8 this hit "No citation possible". A valid book citation is a
    citation."""
    out = risk_assessor(_research(f"Fair use is weighed case by case (ref_id: {S107}).", retrieved=[S107], turn=[S107]))
    assert out["risk_flags"] == []
    assert out["risk_level"] == "low"
    assert out["book_citations"] == [{"ref_id": S107, "valid": True, "citation": CITE_107, "reason": None}]


def test_earlier_turn_ref_passes(store):
    """The check reads the conversation's retrievals, not this turn's: an id an earlier turn retrieved
    is still valid when this turn retrieved nothing."""
    out = risk_assessor(_research(f"As before (ref_id: {S107}).", retrieved=[S107, S108], turn=[]))
    assert out["risk_flags"] == []
    assert out["book_citations"][0]["valid"] is True


def test_never_retrieved_id_is_flagged(store):
    """§108 is in the book, but nothing in the conversation retrieved it."""
    out = risk_assessor(_research(f"Libraries may copy (ref_id: {S108}).", retrieved=[S107], turn=[S107]))
    assert _flag(NOT_RETRIEVED.format(S108)) in out["risk_flags"]
    assert out["risk_level"] == "high"
    assert out["book_citations"] == [
        {"ref_id": S108, "valid": False, "citation": None, "reason": NOT_RETRIEVED.format(S108)}
    ]


def test_sub_path_under_retrieved_chunk(store):
    """§107(1) sits inside §107's chunk: retrieving §107 put it in front of the model. Its citation
    carries its own page, not the chunk's span."""
    out = risk_assessor(_research(f"The first factor is purpose (ref_id: {S107_1}).", retrieved=[S107], turn=[S107]))
    assert out["risk_flags"] == []
    assert out["book_citations"] == [{"ref_id": S107_1, "valid": True, "citation": CITE_107_1, "reason": None}]
    assert output_formatter(out)["report"]["response"] == f"The first factor is purpose ({CITE_107_1})."


def test_a_sub_path_retrieved_by_its_own_id_is_valid(store):
    """lookup_book("§107(1)") marks §107(1) itself as retrieved: valid without its chunk's id."""
    assert check_book_citations(f"ref_id: {S107_1}", [S107_1]) == [
        {"ref_id": S107_1, "valid": True, "citation": CITE_107_1, "reason": None}
    ]


def test_sub_path_under_unretrieved_chunk(store):
    """§106(1) exists, but only inside §106's chunk, which nothing retrieved."""
    out = risk_assessor(_research(f"Reproduction (ref_id: {S106_1}).", retrieved=[S107], turn=[S107]))
    assert _flag(NOT_RETRIEVED.format(S106_1)) in out["risk_flags"]
    assert out["book_citations"][0]["valid"] is False


@pytest.mark.parametrize(
    "written, ref_id",
    [
        ("ref_id: circ92-2025-12:usc17-s9999", "circ92-2025-12:usc17-s9999"),  # on the grammar, not in the book
        (f"ref_id: {OTHER_EDITION}", OTHER_EDITION),  # another edition
        ("ref_id: usc17-s107", "usc17-s107"),  # off the grammar: no book prefix
        ("ref_id: circ92:usc17-s107", "circ92:usc17-s107"),  # off the grammar: no edition
    ],
)
def test_nonexistent_id_is_flagged(store, written, ref_id):
    out = risk_assessor(_research(f"Fair use applies ({written}).", retrieved=[S107], turn=[S107]))
    assert _flag(NOT_RETRIEVED.format(ref_id)) in out["risk_flags"]
    assert out["risk_level"] == "high"
    assert output_formatter(out)["report"]["response"] == f"Fair use applies ([unverified reference: {ref_id}])."


def test_an_id_of_an_unregistered_book_is_invalid_not_a_crash(monkeypatch):
    """Should the store ever hold a node of an edition the registry doesn't list, the citation can't be
    rendered (no book entry): it is invalid, and the turn still answers."""
    node = _node(OTHER_EDITION, "17 U.S.C. §107", pages=(20, 21))
    monkeypatch.setattr("rag.book_citations.fetch_nodes", lambda ref_ids: {OTHER_EDITION: node})
    assert check_book_citations(f"ref_id: {OTHER_EDITION}", [OTHER_EDITION]) == [
        {"ref_id": OTHER_EDITION, "valid": False, "citation": None, "reason": NOT_RETRIEVED.format(OTHER_EDITION)}
    ]


def test_a_retrieved_id_the_store_no_longer_holds_cannot_be_verified(store):
    """Retrieved earlier, gone from the collection since (a re-ingest): nothing to render it from, and
    "not retrieved" would be false."""
    gone = "circ92-2025-12:usc17-s120"
    assert check_book_citations(f"ref_id: {gone}", [gone]) == [
        {"ref_id": gone, "valid": False, "citation": None, "reason": UNVERIFIABLE.format(gone)}
    ]


def test_nothing_cited_never_calls_qdrant(store):
    assert check_book_citations("Per the MSA (doc_id: d1), p. 3.", [S107]) == []
    assert store == []


def test_every_cited_id_is_resolved_in_one_call(store):
    check_book_citations(f"ref_id: {S107} and ref_id: {S108}, again ref_id: {S107}", [S107])
    assert store == [[S107, S108]]


def test_qdrant_failure_flags_every_cited_id(qdrant_down, caplog):
    """Qdrant down while resolving: no citation can be rendered from the store, so every cited id is
    flagged and renders unverified (D13: a risk flag, not a degradation). Logged at ERROR."""
    answer = f"Fair use (ref_id: {S107}); libraries (ref_id: {S108})."
    with caplog.at_level(logging.ERROR, logger="rag.book_citations"):
        out = risk_assessor(_research(answer, retrieved=[S107], turn=[S107]))

    assert _flag(UNVERIFIABLE.format(S107)) in out["risk_flags"]
    assert _flag(UNVERIFIABLE.format(S108)) in out["risk_flags"]
    assert out["risk_level"] == "high"
    assert [c["valid"] for c in out["book_citations"]] == [False, False]
    assert output_formatter(out)["report"]["response"] == (
        f"Fair use ([unverified reference: {S107}]); libraries ([unverified reference: {S108}])."
    )
    assert any(r.levelno == logging.ERROR and "Connection refused" in r.getMessage() for r in caplog.records)


# --- the page check (ruling 12) -------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer, turn",
    [
        # cites a book id; the passage came from an earlier turn
        (f"Fair use (ref_id: {S107}); see p. 12.", []),
        # cites nothing, but this turn read the book
        ("Fair use is set out at page 40 of the book.", [S107]),
    ],
)
def test_model_page_flagged_when_book_used(store, answer, turn):
    out = risk_assessor(_research(answer, retrieved=[S107], turn=turn))
    assert _flag(STATED_PAGE) in out["risk_flags"]
    assert out["risk_level"] == "high"


@pytest.mark.parametrize(
    "state",
    [
        # research over client documents; the conversation read the book on an earlier turn
        _research("Per the MSA (doc_id: d1), the cap is on p. 3 of the MSA.", retrieved=[S107], chunks=[DOC_CHUNK]),
        # doc-chat about the attached contract, after a book turn
        _research("The cap is on p. 3 of the MSA.", retrieved=[S107], uploaded=True),
    ],
)
def test_client_doc_answer_with_page_not_flagged(store, state):
    out = risk_assessor(state)
    assert out["risk_flags"] == []
    assert out["risk_level"] == "low"


def test_the_page_check_reads_the_raw_answer_not_the_rendered_one(store):
    """The rendered citation states a page; the raw answer does not. Only the model's pages count."""
    out = risk_assessor(_research(f"Fair use (ref_id: {S107}).", retrieved=[S107], turn=[S107]))
    assert STATED_PAGE not in _reasons(out)
    assert find_stated_pages(output_formatter(out)["report"]["response"]) == ["pp. 20–21"]


# --- _check_citations ---------------------------------------------------------------------------


def test_doc_only_paths_unchanged():
    """The client-document outcomes, called positionally as before: the book parameters default to
    "no book" and are keyword-only."""
    assert _check_citations("Anything.", []) == [_flag(NO_CITATION_POSSIBLE)]
    assert _check_citations("Anything.", [], True) == []
    assert _check_citations("Per the MSA (doc_id: d1).", [DOC_CHUNK]) == []
    assert _check_citations("Per the Master Services Agreement.", [DOC_CHUNK]) == []
    assert _check_citations("Anything.", [DOC_CHUNK]) == [_flag(NO_CITATION_FOUND)]
    with pytest.raises(TypeError):
        _check_citations("Anything.", [], False, (), True)  # type: ignore[misc]


def test_a_turn_with_the_book_in_the_conversation_and_no_citation_is_not_cited():
    """Something was retrieved (the book, on some turn), so a citation was possible: the answer that
    names nothing is "not found", not "not possible"."""
    assert _check_citations("Anything.", [], book_retrieved=True) == [_flag(NO_CITATION_FOUND)]


def test_an_invalid_book_citation_does_not_count_as_cited():
    invalid = {"ref_id": S108, "valid": False, "citation": None, "reason": NOT_RETRIEVED.format(S108)}
    assert _check_citations("Anything.", [DOC_CHUNK], book_citations=[invalid], book_retrieved=True) == [
        _flag(NO_CITATION_FOUND),
        _flag(NOT_RETRIEVED.format(S108)),
    ]


def test_a_valid_book_citation_counts_beside_client_chunks():
    valid = {"ref_id": S107, "valid": True, "citation": CITE_107, "reason": None}
    assert _check_citations("Anything.", [DOC_CHUNK], book_citations=[valid], book_retrieved=True) == []


def test_an_invalid_citation_is_flagged_on_a_doc_chat_turn_too():
    """The attached document excuses the absence of chunks, never a bad book citation."""
    invalid = {"ref_id": S108, "valid": False, "citation": None, "reason": NOT_RETRIEVED.format(S108)}
    assert _check_citations("Anything.", [], True, book_citations=[invalid], book_retrieved=True) == [
        _flag(NOT_RETRIEVED.format(S108))
    ]


# --- rendering --------------------------------------------------------------------------------------


def test_rendering(store):
    answer = (
        f"Fair use is weighed by four factors (ref_id: {S107}). The first is purpose (`{S107_1}`). "
        f"Compare the MSA (doc_id: d1). See also ref_id: {S108}."
    )
    out = output_formatter(risk_assessor(_research(answer, retrieved=[S107], turn=[S107], chunks=[DOC_CHUNK])))
    report = out["report"]

    assert report["response"] == (
        f"Fair use is weighed by four factors ({CITE_107}). The first is purpose ({CITE_107_1}). "
        f"Compare the MSA (doc_id: d1). See also [unverified reference: {S108}]."
    )
    assert out["llm_response"] == answer  # history and the audit keep the raw ids
    assert report["sources"] == [
        {"doc_id": "d1", "doc_title": "Master Services Agreement"},
        {"ref_id": S107, "citation": CITE_107},
        {"ref_id": S107_1, "citation": CITE_107_1},
    ]


def test_the_formatter_renders_from_state_and_never_resolves_again(qdrant_down):
    """Ruling 13: resolved once. Qdrant failing after risk_assessor changes nothing in the text."""
    state = _research(f"Fair use (ref_id: {S107}).", retrieved=[S107])
    state["book_citations"] = [{"ref_id": S107, "valid": True, "citation": CITE_107, "reason": None}]
    assert output_formatter(state)["report"]["response"] == f"Fair use ({CITE_107})."


def test_a_turn_without_book_citations_renders_the_answer_as_written():
    state = _research("Fair use is a defence.")
    state["book_citations"] = []
    report = output_formatter(state)["report"]
    assert report["response"] == "Fair use is a defence."
    assert report["sources"] == []
