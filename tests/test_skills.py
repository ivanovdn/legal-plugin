# tests/test_skills.py
"""Tests for skill implementations."""

from unittest.mock import patch, MagicMock

from config import get_settings
from skills.contract_generation import contract_generation
from skills.legal_research import legal_research


def _make_state(**overrides):
    base = {
        "request": "test request",
        "user_id": "attorney-1",
        "uploaded_docs": [],
        "task_type": "contract_generation",
        "skill_plan": ["contract_generation"],
        "retrieval_query": "",
        "retrieved_chunks": [],
        "filters": {"client_id": "internal"},
        "messages": [],
        "chat_history": [],
        "llm_response": "",
        "risk_level": "",
        "risk_flags": [],
        "awaiting_review": False,
        "attorney_notes": "",
        "report": {},
        "session_id": "test-sess",
        "checkpoint_ref": "",
        "trace_id": "",
        "review_iterations": 0,
        "report_notes_unincorporated": "",
    }
    base.update(overrides)
    return base


def test_contract_generation_calls_agent(monkeypatch):
    """contract_generation invokes the ReAct agent and sets llm_response."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()

    fake_msg = MagicMock()
    fake_msg.content = "Here is the generated contract:\n\n**SERVICE AGREEMENT**\n\nThis Agreement is entered into..."
    fake_agent_result = {"messages": [fake_msg]}

    with patch("skills.contract_generation.contract_generation._build_agent") as mock_build:
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = fake_agent_result
        mock_build.return_value = mock_agent

        from skills.contract_generation import contract_generation
        state = _make_state(
            request="Generate a service agreement for Client X",
            filters={"client_id": "client-x", "jurisdiction": "US-DE"},
        )
        result = contract_generation(state)

    assert result["llm_response"] != ""
    assert "SERVICE AGREEMENT" in result["llm_response"]


def test_contract_generation_handles_agent_error(monkeypatch):
    """contract_generation handles agent errors gracefully."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()

    with patch("skills.contract_generation.contract_generation._build_agent") as mock_build:
        mock_agent = MagicMock()
        mock_agent.invoke.side_effect = Exception("LLM unavailable")
        mock_build.return_value = mock_agent

        from skills.contract_generation import contract_generation
        state = _make_state(request="Generate a contract")
        result = contract_generation(state)

    assert "Error" in result["llm_response"]


def test_contract_generation_extracts_source_docs(monkeypatch):
    """contract_generation extracts doc_ids from agent messages."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()

    tool_msg = MagicMock()
    tool_msg.content = "Found contract (doc_id: abc12345-6789-0000-1111-222233334444)"
    final_msg = MagicMock()
    final_msg.content = "Generated contract based on doc_id: abc12345-6789-0000-1111-222233334444"
    fake_agent_result = {"messages": [tool_msg, final_msg]}

    with patch("skills.contract_generation.contract_generation._build_agent") as mock_build:
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = fake_agent_result
        mock_build.return_value = mock_agent

        from skills.contract_generation import contract_generation
        state = _make_state(request="Generate a contract")
        result = contract_generation(state)

    assert len(result["retrieved_chunks"]) > 0
    assert result["retrieved_chunks"][0]["doc_id"] == "abc12345-6789-0000-1111-222233334444"


# --- contract_review ---

def test_contract_review_sets_prompt_and_query():
    """contract_review prepares state for rag_retriever + llm_caller."""
    from skills.contract_review import contract_review
    state = _make_state(
        request="Review the indemnification clauses in our latest NDA",
        task_type="contract_review",
    )
    result = contract_review(state)

    assert result["retrieval_query"] != ""
    assert len(result["messages"]) > 0
    assert result["messages"][0]["role"] == "system"
    assert "clause" in result["messages"][0]["content"].lower()


# --- compliance_check ---

def test_compliance_check_sets_prompt_and_query():
    """compliance_check prepares state for policy verification."""
    from skills.compliance_check import compliance_check
    state = _make_state(
        request="Check if our data retention policy complies with GDPR",
        task_type="compliance",
    )
    result = compliance_check(state)

    assert result["retrieval_query"] != ""
    assert len(result["messages"]) > 0
    assert result["messages"][0]["role"] == "system"
    assert "compliance" in result["messages"][0]["content"].lower()


# --- drafting ---

def test_drafting_sets_prompt_and_query():
    """drafting prepares state for document generation."""
    from skills.drafting import drafting
    state = _make_state(
        request="Draft an NDA for a consulting engagement with Acme Corp",
        task_type="drafting",
        filters={"client_id": "internal", "jurisdiction": "US-DE"},
    )
    result = drafting(state)

    assert result["retrieval_query"] != ""
    assert len(result["messages"]) > 0
    assert result["messages"][0]["role"] == "system"
    assert "draft" in result["messages"][0]["content"].lower()


# --- legal_research ---

def test_legal_research_calls_agent(monkeypatch):
    """legal_research invokes the ReAct agent and sets llm_response."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()

    fake_msg = MagicMock()
    fake_msg.content = "Based on the analysis of Contract A (doc_id: d1), the indemnification standard requires..."

    with patch("skills.legal_research._build_agent") as mock_build:
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = {"messages": [fake_msg]}
        mock_build.return_value = mock_agent

        from skills.legal_research import legal_research
        state = _make_state(
            request="What are the indemnification standards in Delaware?",
            task_type="research",
        )
        result = legal_research(state)

    assert result["llm_response"] != ""
    assert "legal_research stub" not in result["llm_response"]


def test_legal_research_handles_error(monkeypatch):
    """legal_research handles agent errors gracefully."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()

    with patch("skills.legal_research._build_agent") as mock_build:
        mock_agent = MagicMock()
        mock_agent.invoke.side_effect = Exception("LLM down")
        mock_build.return_value = mock_agent

        from skills.legal_research import legal_research
        state = _make_state(request="research question")
        result = legal_research(state)

    assert "Error" in result["llm_response"]


# --- chat_history injection into agent skills ---

def test_contract_generation_injects_chat_history_into_agent(monkeypatch):
    """Agent.invoke receives chat_history prepended to the new user message."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    captured = {}
    fake_agent = MagicMock()
    fake_agent.invoke.side_effect = lambda payload: captured.setdefault("payload", payload) or {
        "messages": [MagicMock(content="DRAFT NDA ...")]
    }

    history = [
        {"role": "user", "content": "Generate NDA for ACME"},
        {"role": "assistant", "content": "DRAFT NDA [...]"},
    ]
    state = _make_state(
        request="Make the term 3 years",
        filters={"client_id": "internal"},
        chat_history=history,
    )

    with patch("skills.contract_generation.contract_generation._build_agent", return_value=fake_agent):
        contract_generation(state)

    sent = captured["payload"]["messages"]
    # Expect history first, then the current user request
    assert sent[0] == history[0]
    assert sent[1] == history[1]
    assert sent[-1]["role"] == "user"
    assert "Make the term 3 years" in sent[-1]["content"]


def test_legal_research_injects_chat_history_into_agent(monkeypatch):
    """Same contract, on the research agent."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    captured = {}
    fake_agent = MagicMock()
    fake_agent.invoke.side_effect = lambda payload: captured.setdefault("payload", payload) or {
        "messages": [MagicMock(content="Per case A (doc_id: d1)...")]
    }

    history = [
        {"role": "user", "content": "What's the standard cap?"},
        {"role": "assistant", "content": "2x fees in most cases."},
    ]
    state = _make_state(
        request="And for ACME specifically?",
        filters={"client_id": "internal"},
        chat_history=history,
    )

    with patch("skills.legal_research._build_agent", return_value=fake_agent):
        legal_research(state)

    sent = captured["payload"]["messages"]
    assert sent[0] == history[0]
    assert sent[1] == history[1]
    assert sent[-1]["role"] == "user"
    assert "ACME" in sent[-1]["content"]
