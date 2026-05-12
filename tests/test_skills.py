# tests/test_skills.py
"""Tests for skill implementations."""

from unittest.mock import patch, MagicMock


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


def test_contract_generation_calls_agent(monkeypatch):
    """contract_generation invokes the ReAct agent and sets llm_response."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()

    fake_msg = MagicMock()
    fake_msg.content = "Here is the generated contract:\n\n**SERVICE AGREEMENT**\n\nThis Agreement is entered into..."
    fake_agent_result = {"messages": [fake_msg]}

    with patch("skills.contract_generation._build_agent") as mock_build:
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

    with patch("skills.contract_generation._build_agent") as mock_build:
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

    with patch("skills.contract_generation._build_agent") as mock_build:
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = fake_agent_result
        mock_build.return_value = mock_agent

        from skills.contract_generation import contract_generation
        state = _make_state(request="Generate a contract")
        result = contract_generation(state)

    assert len(result["retrieved_chunks"]) > 0
    assert result["retrieved_chunks"][0]["doc_id"] == "abc12345-6789-0000-1111-222233334444"
