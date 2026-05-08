# tests/test_graph.py
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
    }
    base.update(overrides)
    return base


def test_graph_compiles():
    """Graph compiles without errors."""
    from graph.graph import build_graph
    graph = build_graph()
    assert graph is not None


def test_graph_end_to_end_research():
    """A research request flows through all stubs to END."""
    from graph.graph import build_graph
    graph = build_graph()

    result = graph.invoke(_make_state(
        request="What are the indemnification standards in US contracts?",
    ))

    assert result["task_type"] == "research"
    assert "legal_research stub" in result["llm_response"]
    assert result["risk_level"] == "low"


def test_graph_contract_generation_routes_to_human_review():
    """Contract generation always routes through human_review."""
    from graph.graph import build_graph
    graph = build_graph()

    result = graph.invoke(_make_state(
        request="Generate a service agreement",
        task_type="contract_generation",
        skill_plan=["contract_generation"],
    ))

    assert result["task_type"] == "contract_generation"
    assert result["awaiting_review"] is True
    assert "contract_generation stub" in result["llm_response"]


def test_graph_drafting_routes_to_human_review():
    """Drafting always routes through human_review."""
    from graph.graph import build_graph
    graph = build_graph()

    result = graph.invoke(_make_state(
        request="Draft an NDA",
        task_type="drafting",
        skill_plan=["drafting"],
    ))

    assert result["awaiting_review"] is True
    assert "drafting stub" in result["llm_response"]


def test_graph_multi_skill_routes_through_planner():
    """When skill_plan has multiple skills, routes through planner."""
    from graph.graph import build_graph
    graph = build_graph()

    result = graph.invoke(_make_state(
        request="Review contract and check compliance",
        task_type="compliance",
        skill_plan=["contract_review", "compliance"],
    ))

    assert result["task_type"] == "compliance"
