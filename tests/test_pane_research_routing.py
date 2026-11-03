# tests/test_pane_research_routing.py
"""A research turn from the Word pane is always doc-chat, even on an empty document (final review I1).

The pane's Chat tab sends `readBody()`, which is "" for a blank document. Before this, the query route built
`uploaded_docs` only from non-empty text, so a blank document took the KB path — the research agent, which
has the reference-book tools — and an invalid citation or a model-written page there interrupted a turn the
pane cannot resume, showing `interrupt_payload.llm_response` raw (ChatTab). The pane is told apart by the
headers it sends on every request (api/client_info.py); Chainlit and direct API callers send none and keep
the KB path.

"A document is attached" is one predicate, `has_attached_document` (graph/state.py): legal_research's branch
and risk_assessor's book check read the same thing.
"""
import importlib

from fastapi.testclient import TestClient

import api.routes.query as q
from api.main import app
from graph.graph import build_graph
from graph.nodes.risk_assessor import risk_assessor
from graph.state import has_attached_document

PANE_HEADERS = {"X-User-ID": "atty-1", "X-Office-Platform": "Mac", "X-Office-Version": "16.89",
                "X-Pane-Build": "abc1234"}
DOC_CHAT_ANSWER = "The document is empty, so there is nothing in it to answer from."
BOOK_REF = "circ92-2025-12:usc17-s107"


class _Reply:
    def __init__(self, content):
        self.content = content
        self.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        self.response_metadata = {"model": "qwen3.6:latest"}


class _LLM:
    def __init__(self, content):
        self._content = content
        self.calls: list[list[dict]] = []

    def invoke(self, messages):
        self.calls.append(messages)
        return _Reply(self._content)


class _Agent:
    """The research agent's run: it answers with a book id nothing retrieved and a page of its own, so a
    turn that reached it and its check would be flagged high."""
    def invoke(self, payload, config=None):
        return {"messages": [_Reply(f"Fair use is in ref_id: {BOOK_REF}, p. 12.")]}


def _wire(monkeypatch):
    """The real graph (no checkpointer), the LLM and the agent replaced, and a record of what ran."""
    research = importlib.import_module("skills.legal_research.legal_research")
    ran = {"agent_built": 0, "book_checks": []}
    llm = _LLM(DOC_CHAT_ANSWER)

    def build_agent():
        ran["agent_built"] += 1
        return _Agent()

    def check_book_citations(text, retrieved):
        ran["book_checks"].append(text)
        return [{"ref_id": BOOK_REF, "valid": False, "citation": None,
                 "reason": f"Cites a reference that was not retrieved: {BOOK_REF}"}]

    monkeypatch.setattr(research, "_build_agent", build_agent)
    monkeypatch.setattr(research, "_build_llm", lambda: llm)
    monkeypatch.setattr("graph.nodes.risk_assessor.check_book_citations", check_book_citations)
    monkeypatch.setattr("graph.nodes.rag_retriever.hybrid_search", lambda *a, **k: [])
    graph = build_graph(checkpointer=None)
    monkeypatch.setattr(q, "_get_graph", lambda: graph)
    monkeypatch.setattr(q, "_checkpointer_active", True)
    monkeypatch.setattr(q, "refresh_ttl", lambda *a, **k: None)
    return ran, llm


def _ask(headers, text=""):
    return TestClient(app).post(
        "/api/query",
        json={"request": "What does fair use cover?", "task_type": "research", "uploaded_text": text,
              "filters": {"client_id": "internal"}, "document_uuid": "doc-blank-1"},
        headers=headers,
    ).json()


def test_a_pane_research_turn_on_an_empty_document_is_doc_chat(monkeypatch):
    """RED first: it took the KB path. Mutation: build uploaded_docs from the text alone again."""
    ran, llm = _wire(monkeypatch)

    resp = _ask(PANE_HEADERS)

    assert resp["status"] == "ok"
    assert ran["agent_built"] == 0
    assert ran["book_checks"] == []
    assert len(llm.calls) == 1  # one doc-chat call, on the empty document
    assert "--- ATTACHED DOCUMENT" in llm.calls[0][-1]["content"]
    data = resp["data"]
    assert data["awaiting_review"] is False
    assert data["report"]["response"] == DOC_CHAT_ANSWER
    assert data["risk_level"] == "low"


def test_either_pane_header_alone_marks_the_pane(monkeypatch):
    ran, _ = _wire(monkeypatch)

    _ask({"X-User-ID": "atty-1", "X-Pane-Build": "dev"})
    _ask({"X-User-ID": "atty-1", "X-Office-Platform": "PC"})

    assert ran["agent_built"] == 0 and ran["book_checks"] == []


def test_a_request_without_the_pane_headers_still_takes_the_kb_path_on_empty_text(monkeypatch):
    """Chainlit and direct API callers send no pane headers: no document means the research agent, with
    the book, and its answer is checked (here: flagged, so the turn stops for review)."""
    ran, llm = _wire(monkeypatch)

    resp = _ask({"X-User-ID": "atty-1"})

    assert ran["agent_built"] == 1
    assert len(ran["book_checks"]) == 1
    assert llm.calls == []
    assert resp["data"]["awaiting_review"] is True


def test_a_pane_review_on_an_empty_document_is_unchanged(monkeypatch):
    """Only a research turn is forced to doc-chat: a review of "" still sends no document."""
    seen = {}

    def invoke(state, config=None):
        seen.update(state)
        return {**state, "llm_response": "done", "report": {}}

    graph = type("G", (), {"invoke": staticmethod(invoke)})()
    monkeypatch.setattr(q, "_get_graph", lambda: graph)
    monkeypatch.setattr(q, "_checkpointer_active", True)
    monkeypatch.setattr(q, "refresh_ttl", lambda *a, **k: None)

    TestClient(app).post("/api/query", json={"request": "Review this contract.", "task_type": "contract_review",
                                             "uploaded_text": ""}, headers=PANE_HEADERS)

    assert seen["uploaded_docs"] == []


# --- the one predicate --------------------------------------------------------------------------------

def test_a_document_is_attached_when_uploaded_docs_is_non_empty_whatever_its_text():
    assert has_attached_document({"uploaded_docs": [{"text": ""}]}) is True
    assert has_attached_document({"uploaded_docs": [{"text": "NDA"}]}) is True
    assert has_attached_document({"uploaded_docs": []}) is False
    assert has_attached_document({}) is False


def test_legal_research_on_an_attached_empty_document_is_doc_chat_and_does_not_crash(monkeypatch):
    """The skill's own branch reads the predicate, not the text: an attached "" is a document."""
    research = importlib.import_module("skills.legal_research.legal_research")
    ran, llm = _wire(monkeypatch)
    state = {"request": "Who signs?", "user_id": "atty-1", "uploaded_docs": [{"text": ""}],
             "filters": {"client_id": "internal"}, "document_id": "doc-blank-2", "chat_history": []}

    out = research.legal_research(state)

    assert ran["agent_built"] == 0
    assert out["llm_response"] == DOC_CHAT_ANSWER
    assert out["book_refs_turn"] == []
    assert out["context_truncated"] is None


def test_risk_assessor_skips_the_book_check_on_an_attached_empty_document(monkeypatch):
    ran, _ = _wire(monkeypatch)
    state = {"task_type": "research", "llm_response": f"ref_id: {BOOK_REF}, p. 12",
             "uploaded_docs": [{"text": ""}], "retrieved_chunks": [], "book_refs_retrieved": [BOOK_REF],
             "book_refs_turn": []}

    out = risk_assessor(state)

    assert ran["book_checks"] == []
    assert (out["risk_level"], out["risk_flags"], out["book_citations"]) == ("low", [], [])
