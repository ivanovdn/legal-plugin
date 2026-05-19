# tests/test_graph.py
from unittest.mock import patch, MagicMock
from graph.state import LegalAgentState


def test_legal_agent_state_can_be_created():
    """LegalAgentState can be instantiated with required fields."""
    state: LegalAgentState = {
        "request": "Review this contract",
        "user_id": "attorney-1",
        "uploaded_docs": [],
        "task_type": "",
        "skill_plan": [],
        "retrieval_query": "",
        "retrieved_chunks": [],
        "filters": {},
        "messages": [],
        "llm_response": "",
        "risk_level": "low",
        "risk_flags": [],
        "awaiting_review": False,
        "attorney_notes": "",
        "report": {},
        "session_id": "sess-001",
        "checkpoint_ref": "",
        "trace_id": "",
        "chat_history": [],
    }
    assert state["request"] == "Review this contract"
    assert state["risk_level"] == "low"
    assert state["task_type"] == ""


def _make_state(**overrides) -> LegalAgentState:
    """Helper to create a state dict with defaults."""
    base = {
        "request": "test request",
        "user_id": "attorney-1",
        "uploaded_docs": [],
        "task_type": "",
        "skill_plan": [],
        "retrieval_query": "",
        "retrieved_chunks": [],
        "filters": {},
        "messages": [],
        "llm_response": "",
        "risk_level": "",
        "risk_flags": [],
        "awaiting_review": False,
        "attorney_notes": "",
        "report": {},
        "session_id": "test-sess",
        "checkpoint_ref": "",
        "trace_id": "",
        "chat_history": [],
    }
    base.update(overrides)
    return base


def test_graph_compiles():
    """Graph compiles without errors."""
    from graph.graph import build_graph
    graph = build_graph()
    assert graph is not None


def _fake_ollama_post(url, **kwargs):
    """Mock Ollama that handles both intent classification and LLM calls."""
    resp = MagicMock()
    resp.status_code = 200
    body = kwargs.get("json", {})
    messages = body.get("messages", [])
    user_msg = messages[-1]["content"] if messages else ""

    if "classify" in user_msg.lower() or "task type" in user_msg.lower():
        resp.json.return_value = {"message": {"content": '{"task_type": "research"}'}}
    else:
        resp.json.return_value = {"message": {"content": "Based on Contract A (doc_id: d1), the answer is X."}}
    return resp


_fake_chunks = [
    {"chunk_id": "c1", "doc_id": "d1", "doc_title": "Contract A",
     "text": "relevant legal text", "rrf_score": 0.8,
     "doc_type": "contract", "client_id": "internal", "jurisdiction": "US"},
]


def _fake_agent():
    """Mock agent that returns a simple response."""
    mock_agent = MagicMock()
    fake_msg = MagicMock()
    fake_msg.content = "Based on Contract A (doc_id: d1), the analysis shows relevant findings."
    mock_agent.invoke.return_value = {"messages": [fake_msg]}
    return mock_agent


def test_graph_end_to_end_research(tmp_path, monkeypatch):
    """A research request flows through real nodes to END."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("SQLITE_PATH", db_path)
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")

    from config import get_settings
    get_settings.cache_clear()

    from memory.audit import init_audit_db
    init_audit_db(db_path)

    import graph.nodes.memory_writer as mw
    mw._db_initialized = True

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=_fake_chunks), \
         patch("skills.legal_research._build_agent", return_value=_fake_agent()), \
         patch("skills.contract_generation.contract_generation._build_agent", return_value=_fake_agent()):

        from graph.graph import build_graph
        graph = build_graph()
        result = graph.invoke(_make_state(
            request="What are indemnification standards?",
        ))

    assert result["task_type"] == "research"
    assert result["llm_response"] != ""
    assert "response" in result["report"]


def test_graph_contract_generation_routes_to_human_review(tmp_path, monkeypatch):
    """Contract generation always routes through human_review."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("SQLITE_PATH", db_path)
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")

    from config import get_settings
    get_settings.cache_clear()

    from memory.audit import init_audit_db
    init_audit_db(db_path)

    import graph.nodes.memory_writer as mw
    mw._db_initialized = True

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=_fake_chunks), \
         patch("skills.contract_generation.contract_generation._build_agent", return_value=_fake_agent()):

        from graph.graph import build_graph
        graph = build_graph()
        result = graph.invoke(_make_state(
            request="Generate a service agreement",
            task_type="contract_generation",
            skill_plan=["contract_generation"],
        ))

    assert result["task_type"] == "contract_generation"
    assert result["awaiting_review"] is True


def test_graph_drafting_routes_to_human_review(tmp_path, monkeypatch):
    """Drafting always routes through human_review."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("SQLITE_PATH", db_path)
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")

    from config import get_settings
    get_settings.cache_clear()

    from memory.audit import init_audit_db
    init_audit_db(db_path)

    import graph.nodes.memory_writer as mw
    mw._db_initialized = True

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=_fake_chunks):

        from graph.graph import build_graph
        graph = build_graph()
        result = graph.invoke(_make_state(
            request="Draft an NDA",
            task_type="drafting",
            skill_plan=["drafting"],
        ))

    assert result["awaiting_review"] is True


def test_graph_full_flow_with_audit(tmp_path, monkeypatch):
    """Full graph flow: intake -> ... -> memory_writer writes audit log."""
    db_path = str(tmp_path / "test_legal.db")
    monkeypatch.setenv("SQLITE_PATH", db_path)
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")

    from config import get_settings
    get_settings.cache_clear()

    from memory.audit import init_audit_db
    init_audit_db(db_path)

    import graph.nodes.memory_writer as mw
    mw._db_initialized = True

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=_fake_chunks), \
         patch("skills.legal_research._build_agent", return_value=_fake_agent()), \
         patch("skills.contract_generation.contract_generation._build_agent", return_value=_fake_agent()):

        from graph.graph import build_graph
        graph = build_graph()
        result = graph.invoke(_make_state(
            request="What are indemnification standards?",
            session_id="integration-test",
        ))

    assert result["task_type"] == "research"
    assert result["llm_response"] != ""
    assert "response" in result["report"]

    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT * FROM audit_log").fetchall()
    conn.close()
    assert len(rows) >= 1
