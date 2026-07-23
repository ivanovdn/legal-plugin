# tests/test_graph.py
from unittest.mock import patch, MagicMock

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from config import get_settings
from graph.graph import build_graph, route_review
from graph.state import LegalAgentState
from memory.db import get_pool


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
        "review_iterations": 0,
        "report_notes_unincorporated": "",
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
        "review_iterations": 0,
        "report_notes_unincorporated": "",
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
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")

    from config import get_settings
    get_settings.cache_clear()

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
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")

    from config import get_settings
    get_settings.cache_clear()

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=_fake_chunks), \
         patch("skills.contract_generation.contract_generation._build_agent", return_value=_fake_agent()), \
         patch("graph.nodes.human_review.interrupt", return_value={"approved": True, "notes": "", "revised_response": ""}):

        from graph.graph import build_graph
        graph = build_graph()
        result = graph.invoke(_make_state(
            request="Generate a service agreement",
            task_type="contract_generation",
            skill_plan=["contract_generation"],
        ))

    assert result["task_type"] == "contract_generation"
    assert result["awaiting_review"] is False  # interrupt returned approval, exit cleanly
    assert result.get("report", {}).get("response") != ""  # routed through output_formatter


def test_graph_drafting_routes_to_human_review(tmp_path, monkeypatch):
    """Drafting always routes through human_review."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")

    from config import get_settings
    get_settings.cache_clear()

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=_fake_chunks), \
         patch("graph.nodes.human_review.interrupt", return_value={"approved": True, "notes": "", "revised_response": ""}):

        from graph.graph import build_graph
        graph = build_graph()
        result = graph.invoke(_make_state(
            request="Draft an NDA",
            task_type="drafting",
            skill_plan=["drafting"],
        ))

    assert result["task_type"] == "drafting"
    assert result["awaiting_review"] is False  # interrupt returned approval, exit cleanly
    assert result.get("report", {}).get("response") != ""  # routed through output_formatter


_REVIEW_BLOCKER_OUTPUT = """# Review Summary
Overall status: Do not send for signature
Contract type: MSA / Counterparty: Acme

# Key Findings
| Issue ID | Clause / section | Rating | Issue | Required action | Owner |
| MSA-001 | Liability / Cap | Red | Uncapped liability | Add 12-month cap | CLCO |

# No Signature Checklist Result
Overall status: Do not send for signature
Blocking items: MSA-001 / Final recommendation: escalate to CLCO
"""


def _fake_ollama_review_blocker(url, **kwargs):
    """Mock Ollama: classify → contract_review; otherwise → a blocker review."""
    resp = MagicMock()
    resp.status_code = 200
    body = kwargs.get("json", {})
    messages = body.get("messages", [])
    user_msg = messages[-1]["content"] if messages else ""
    if "classify" in user_msg.lower() or "task type" in user_msg.lower():
        resp.json.return_value = {"message": {"content": '{"task_type": "contract_review"}'}}
    else:
        resp.json.return_value = {"message": {"content": _REVIEW_BLOCKER_OUTPUT}}
    return resp


def test_graph_contract_review_blocker_interactive_pauses(tmp_path, monkeypatch):
    """A blocker review from an interactive caller (Chainlit) pauses at human_review."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")
    monkeypatch.setenv("INTERRUPT_ENABLED", "true")
    get_settings.cache_clear()

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_ollama_review_blocker), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_ollama_review_blocker), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=[]):

        compiled = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "thread-review-1"}}
        state = _make_state(
            request="Review this contract.",
            task_type="contract_review",
            skill_plan=["contract_review"],
            uploaded_docs=[{"text": "MASTER SERVICES AGREEMENT between Acme and Trinetix."}],
        )
        state["interactive_review"] = True
        result = compiled.invoke(state, config=config)

    assert "__interrupt__" in result
    assert result["__interrupt__"][0].value.get("type") == "human_review"
    assert result.get("risk_level") == "high"


def test_graph_contract_review_blocker_noninteractive_completes(tmp_path, monkeypatch):
    """A blocker review from a non-interactive caller (Word) does NOT interrupt;
    it completes through output_formatter and carries report.requires_attorney."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")
    monkeypatch.setenv("INTERRUPT_ENABLED", "true")
    get_settings.cache_clear()

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_ollama_review_blocker), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_ollama_review_blocker), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=[]), \
         patch("graph.nodes.human_review.interrupt") as mock_interrupt:

        compiled = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "thread-review-2"}}
        state = _make_state(
            request="Review this contract.",
            task_type="contract_review",
            skill_plan=["contract_review"],
            uploaded_docs=[{"text": "MASTER SERVICES AGREEMENT between Acme and Trinetix."}],
        )
        state["interactive_review"] = False
        result = compiled.invoke(state, config=config)

    mock_interrupt.assert_not_called()
    assert "__interrupt__" not in result
    assert result.get("risk_level") == "high"
    assert result["report"]["requires_attorney"] is True
    assert result["report"]["response"] != ""


def test_graph_full_flow_with_audit(tmp_path, monkeypatch):
    """Full graph flow: intake -> ... -> memory_writer writes audit log."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")

    from config import get_settings
    get_settings.cache_clear()

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

    with get_pool().connection() as conn:
        rows = conn.execute("SELECT * FROM audit_log").fetchall()
    assert len(rows) >= 1


def test_graph_includes_history_appender_node():
    """history_appender is registered as a graph node."""
    compiled = build_graph()
    assert "history_appender" in compiled.nodes


def test_graph_history_appender_runs_before_memory_writer(tmp_path, monkeypatch):
    """In a full graph invocation, history_appender produces chat_history before memory_writer."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")
    get_settings.cache_clear()

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=_fake_chunks), \
         patch("skills.legal_research._build_agent", return_value=_fake_agent()), \
         patch("skills.contract_generation.contract_generation._build_agent", return_value=_fake_agent()):

        compiled = build_graph()
        result = compiled.invoke(_make_state(request="What are indemnification standards?"))

    assert len(result["chat_history"]) == 2
    assert result["chat_history"][0]["role"] == "user"
    assert result["chat_history"][0]["content"] == "What are indemnification standards?"
    assert result["chat_history"][1]["role"] == "assistant"
    assert result["chat_history"][1]["content"] != ""


def test_graph_with_checkpointer_persists_chat_history_across_invocations(tmp_path, monkeypatch):
    """Two invocations with the same thread_id: turn 2's LLM prompt contains turn 1's messages."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")
    get_settings.cache_clear()

    captured_llm_inputs = []

    def _capture_aware_ollama(url, **kwargs):
        """Same as _fake_ollama_post but also records each request's messages."""
        body = kwargs.get("json", {})
        captured_llm_inputs.append(body.get("messages", []))
        return _fake_ollama_post(url, **kwargs)

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_capture_aware_ollama), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_capture_aware_ollama), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=_fake_chunks), \
         patch("skills.legal_research._build_agent", return_value=_fake_agent()), \
         patch("skills.contract_generation.contract_generation._build_agent", return_value=_fake_agent()):

        compiled = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "test-thread-xyz"}}

        # Turn 1 — force compliance routing so the deterministic llm_caller runs
        # (research routes to the legal_research ReAct agent which skips llm_caller).
        state_1 = _make_state(
            request="What are indemnification standards?",
            task_type="compliance",
            skill_plan=["compliance"],
        )
        result_1 = compiled.invoke(state_1, config=config)
        assert len(result_1["chat_history"]) == 2

        # Turn 2 — same thread, fresh request, empty chat_history in initial state
        state_2 = _make_state(
            request="And for software vendors specifically?",
            task_type="compliance",
            skill_plan=["compliance"],
            chat_history=[],
        )
        result_2 = compiled.invoke(state_2, config=config)

    # After turn 2, chat_history should be exactly 4 entries: 2 from turn 1 + 2 from
    # turn 2. The reducer's idempotency guard (in graph/state.py) means upstream
    # nodes forwarding state unchanged don't trigger doubling.
    assert len(result_2["chat_history"]) == 4
    assert result_2["chat_history"][0]["content"] == "What are indemnification standards?"
    assert result_2["chat_history"][1]["role"] == "assistant"
    assert result_2["chat_history"][2]["content"] == "And for software vendors specifically?"
    assert result_2["chat_history"][3]["role"] == "assistant"

    # Turn 2's llm_caller invocation (the last LLM call) should have included
    # turn 1's user message in its prompt via the chat_history injection.
    turn_2_llm_messages = captured_llm_inputs[-1]
    contents = [m.get("content", "") for m in turn_2_llm_messages]
    assert any("indemnification standards" in c for c in contents), \
        f"Turn 2 LLM prompt should reference turn 1's user message. Got contents: {contents}"


def test_graph_without_checkpointer_still_works(tmp_path, monkeypatch):
    """Regression: graph compiled with checkpointer=None still runs end-to-end."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")
    get_settings.cache_clear()

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=_fake_chunks), \
         patch("skills.legal_research._build_agent", return_value=_fake_agent()), \
         patch("skills.contract_generation.contract_generation._build_agent", return_value=_fake_agent()):

        compiled = build_graph(checkpointer=None)
        result = compiled.invoke(_make_state(request="hello"))

    # History was still appended (just not persisted across invocations)
    assert len(result["chat_history"]) == 2


def test_route_review_returns_skill_dispatcher_when_llm_response_empty():
    """Empty llm_response is the loop-back signal."""
    state = _make_state(llm_response="", awaiting_review=False)
    assert route_review(state) == "skill_dispatcher"


def test_route_review_returns_output_formatter_when_llm_response_set():
    """Non-empty llm_response means terminal exit (approve/revise/cap)."""
    state = _make_state(llm_response="DRAFT", awaiting_review=False)
    assert route_review(state) == "output_formatter"


def test_graph_interrupts_and_returns_partial_state_with_memory_saver(tmp_path, monkeypatch):
    """First invoke pauses at human_review; result has awaiting_review=True."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")
    monkeypatch.setenv("INTERRUPT_ENABLED", "true")
    get_settings.cache_clear()

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=_fake_chunks), \
         patch("skills.contract_generation.contract_generation._build_agent", return_value=_fake_agent()):

        compiled = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "thread-pause-1"}}

        state = _make_state(
            request="Generate a service agreement for Vertex",
            task_type="contract_generation",
            skill_plan=["contract_generation"],
        )
        result = compiled.invoke(state, config=config)

    # The graph paused — LangGraph signals pause via __interrupt__ on the
    # returned partial state (in-flight node mutations are not committed
    # to the checkpoint when interrupt() raises).
    assert "__interrupt__" in result
    interrupts = result["__interrupt__"]
    assert len(interrupts) == 1
    assert interrupts[0].value.get("type") == "human_review"
    assert result.get("task_type") == "contract_generation"
    assert result.get("llm_response", "") != ""


def test_graph_resume_with_approved_completes(tmp_path, monkeypatch):
    """After pause, resume with approved=True → final result, awaiting_review=False."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")
    monkeypatch.setenv("INTERRUPT_ENABLED", "true")
    get_settings.cache_clear()

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=_fake_chunks), \
         patch("skills.contract_generation.contract_generation._build_agent", return_value=_fake_agent()):

        compiled = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "thread-approve-1"}}

        compiled.invoke(_make_state(
            request="Generate a service agreement for Vertex",
            task_type="contract_generation",
            skill_plan=["contract_generation"],
        ), config=config)

        final = compiled.invoke(
            Command(resume={"approved": True, "notes": "", "revised_response": ""}),
            config=config,
        )

    assert final.get("awaiting_review") is False
    assert final.get("report", {}).get("response", "") != ""


def test_graph_resume_with_notes_loops_back_and_regenerates(tmp_path, monkeypatch):
    """Resume with notes-only → graph loops back, regenerates, pauses again at higher iter."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")
    monkeypatch.setenv("INTERRUPT_ENABLED", "true")
    monkeypatch.setenv("MAX_REVIEW_ITERATIONS", "3")
    get_settings.cache_clear()

    # Track agent (first gen) and direct LLM (revision) invocations separately.
    # On loop-back, contract_generation bypasses the ReAct agent and does a
    # single ChatOllama.invoke for speed.
    agent_invocations = []
    revise_invocations = []

    def _capture_agent():
        agent = MagicMock()
        def _invoke(payload):
            agent_invocations.append(payload)
            return {"messages": [MagicMock(content=f"DRAFT v{len(agent_invocations)} (doc_id: d1)")]}
        agent.invoke.side_effect = _invoke
        return agent

    def _capture_llm(*_args, **_kwargs):
        llm = MagicMock()
        def _invoke(messages):
            revise_invocations.append(messages)
            return MagicMock(content=f"REVISED v{len(revise_invocations)}")
        llm.invoke.side_effect = _invoke
        return llm

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=_fake_chunks), \
         patch("skills.contract_generation.contract_generation._build_agent", return_value=_capture_agent()), \
         patch("skills.contract_generation.contract_generation.ChatOllama", side_effect=_capture_llm):

        compiled = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "thread-loop-1"}}

        # Turn 1: invoke → pause
        compiled.invoke(_make_state(
            request="Generate a service agreement for Vertex",
            task_type="contract_generation",
            skill_plan=["contract_generation"],
        ), config=config)

        # Resume with notes only → graph loops back, regenerates via direct LLM, pauses again
        result_2 = compiled.invoke(
            Command(resume={
                "approved": False,
                "notes": "Add a confidentiality clause",
                "revised_response": "",
            }),
            config=config,
        )

    # Loop-back regenerated and paused again at human_review.
    # LangGraph signals the second pause via __interrupt__; in-flight mutations
    # (like awaiting_review=True) aren't committed to checkpoint state.
    assert "__interrupt__" in result_2
    assert result_2["__interrupt__"][0].value.get("type") == "human_review"
    assert result_2["__interrupt__"][0].value.get("review_iterations") == 1
    assert result_2.get("review_iterations") == 1
    # Agent ran once for initial generation; revision used direct LLM
    assert len(agent_invocations) == 1
    assert len(revise_invocations) == 1
    revise_user_msg = revise_invocations[0][-1]["content"]
    assert "PREVIOUS DRAFT" in revise_user_msg
    assert "ATTORNEY REVISION NOTES" in revise_user_msg
    assert "confidentiality clause" in revise_user_msg


def test_graph_resume_iteration_cap_terminates(tmp_path, monkeypatch):
    """3 successive notes-only resumes → 4th resume terminates with unincorporated notes in report."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")
    monkeypatch.setenv("INTERRUPT_ENABLED", "true")
    monkeypatch.setenv("MAX_REVIEW_ITERATIONS", "3")
    get_settings.cache_clear()

    def _fake_revise_llm(*_args, **_kwargs):
        llm = MagicMock()
        llm.invoke.return_value = MagicMock(content="REVISED draft")
        return llm

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=_fake_chunks), \
         patch("skills.contract_generation.contract_generation._build_agent", return_value=_fake_agent()), \
         patch("skills.contract_generation.contract_generation.ChatOllama", side_effect=_fake_revise_llm):

        compiled = build_graph(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "thread-cap-1"}}

        # Initial invoke
        compiled.invoke(_make_state(
            request="Generate a service agreement for Vertex",
            task_type="contract_generation",
            skill_plan=["contract_generation"],
        ), config=config)

        # 3 loop-back resumes (iter 1, 2, 3)
        for i in range(3):
            compiled.invoke(
                Command(resume={
                    "approved": False,
                    "notes": f"iteration {i + 1} change",
                    "revised_response": "",
                }),
                config=config,
            )

        # 4th resume with notes — should hit the cap and terminate
        final = compiled.invoke(
            Command(resume={
                "approved": False,
                "notes": "this should not be incorporated",
                "revised_response": "",
            }),
            config=config,
        )

    assert final.get("awaiting_review") is False
    assert final.get("report", {}).get("notes_unincorporated") == "this should not be incorporated"


def test_graph_without_checkpointer_still_completes_in_one_shot(tmp_path, monkeypatch):
    """Regression: with no checkpointer + interrupt disabled, graph completes as before."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("RERANKER_ENABLED", "false")
    monkeypatch.setenv("BM25_ENABLED", "false")
    monkeypatch.setenv("INTERRUPT_ENABLED", "false")
    get_settings.cache_clear()

    with patch("graph.nodes.intent_router.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.llm_caller.httpx.post", side_effect=_fake_ollama_post), \
         patch("graph.nodes.rag_retriever.hybrid_search", return_value=_fake_chunks), \
         patch("skills.contract_generation.contract_generation._build_agent", return_value=_fake_agent()):

        compiled = build_graph(checkpointer=None)
        result = compiled.invoke(_make_state(
            request="Generate a service agreement for Vertex",
            task_type="contract_generation",
            skill_plan=["contract_generation"],
        ))

    # With interrupt disabled, graph completes through output_formatter
    assert result.get("report", {}).get("response", "") != ""
