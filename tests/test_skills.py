# tests/test_skills.py
"""Tests for skill implementations."""

import importlib
import sys
from unittest.mock import patch, MagicMock

from config import get_settings
from skills.compliance_check import compliance_check
from skills.contract_generation import contract_generation
from skills.contract_review.contract_review import contract_review
from skills.drafting import drafting
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


# --- attorney_notes injection into agent skills ---

def test_contract_generation_injects_attorney_notes(monkeypatch):
    """When attorney_notes is set, the agent's user message includes the notes block."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    captured = {}
    fake_agent = MagicMock()
    fake_agent.invoke.side_effect = lambda payload: (
        captured.setdefault("payload", payload) or
        {"messages": [MagicMock(content="DRAFT v2")]}
    )

    state = _make_state(
        request="Generate a service agreement for Vertex",
        filters={"client_id": "internal"},
        attorney_notes="Add a confidentiality clause; reduce cap to 1.5x.",
    )

    with patch("skills.contract_generation.contract_generation._build_agent", return_value=fake_agent):
        contract_generation(state)

    sent = captured["payload"]["messages"]
    # Final user message must contain both the request and the attorney notes block
    last_user = sent[-1]["content"]
    assert "Vertex" in last_user
    assert "ATTORNEY REVIEW NOTES" in last_user
    assert "confidentiality clause" in last_user


def test_legal_research_injects_attorney_notes(monkeypatch):
    """Same contract for the research agent."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    captured = {}
    fake_agent = MagicMock()
    fake_agent.invoke.side_effect = lambda payload: (
        captured.setdefault("payload", payload) or
        {"messages": [MagicMock(content="Per case A (doc_id: d1)...")]}
    )

    state = _make_state(
        request="What's the standard cap for SaaS?",
        filters={"client_id": "internal"},
        attorney_notes="Focus on EU jurisdiction precedents only.",
    )

    with patch("skills.legal_research._build_agent", return_value=fake_agent):
        legal_research(state)

    sent = captured["payload"]["messages"]
    last_user = sent[-1]["content"]
    assert "ATTORNEY REVIEW NOTES" in last_user
    assert "EU jurisdiction" in last_user


# --- attorney_notes injection into plain skills ---

def test_contract_review_injects_attorney_notes(monkeypatch):
    """contract_review's user message includes the attorney_notes block."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    state = _make_state(
        request="Review this NDA",
        attorney_notes="Pay special attention to clauses 3 and 7.",
    )
    result = contract_review(state)
    last_user = result["messages"][-1]["content"]
    assert "ATTORNEY REVIEW NOTES" in last_user
    assert "clauses 3 and 7" in last_user


def test_compliance_check_injects_attorney_notes(monkeypatch):
    """compliance_check user message includes the attorney_notes block."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    state = _make_state(
        request="Check GDPR compliance",
        attorney_notes="Focus on data-subject rights specifically.",
    )
    result = compliance_check(state)
    last_user = result["messages"][-1]["content"]
    assert "ATTORNEY REVIEW NOTES" in last_user
    assert "data-subject rights" in last_user


def test_drafting_injects_attorney_notes(monkeypatch):
    """drafting user message includes the attorney_notes block."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    state = _make_state(
        request="Draft an NDA template",
        attorney_notes="Use the mutual-NDA format.",
    )
    result = drafting(state)
    last_user = result["messages"][-1]["content"]
    assert "ATTORNEY REVIEW NOTES" in last_user
    assert "mutual-NDA" in last_user


def test_extract_proposed_edits_parses_well_formed_block():
    """A single well-formed JSON block is parsed into a structured proposal."""
    from skills.legal_research import _extract_proposed_edits

    prose = (
        "Here's a tighter version of the cap.\n"
        "```json\n"
        '{"action": "replace", "target_text": "the fees paid", '
        '"new_text": "2x the fees paid", "rationale": "Aligns with playbook"}\n'
        "```"
    )
    edits = _extract_proposed_edits(prose)
    assert len(edits) == 1
    assert edits[0]["action"] == "replace"
    assert edits[0]["new_text"] == "2x the fees paid"


def test_extract_proposed_edits_parses_multiple_blocks():
    """Multiple JSON blocks yield multiple proposals in order."""
    from skills.legal_research import _extract_proposed_edits

    prose = (
        "Two alternatives:\n"
        '```json\n{"action": "replace", "target_text": "X", "new_text": "Y"}\n```\n'
        "Or:\n"
        '```json\n{"action": "insert", "anchor_text": "Section 7", '
        '"position": "after", "new_text": "Force majeure..."}\n```'
    )
    edits = _extract_proposed_edits(prose)
    assert len(edits) == 2
    assert edits[0]["action"] == "replace"
    assert edits[1]["action"] == "insert"
    assert edits[1]["position"] == "after"


def test_extract_proposed_edits_skips_malformed_json():
    """Malformed JSON blocks are logged and skipped, not propagated."""
    from skills.legal_research import _extract_proposed_edits

    prose = (
        '```json\n{"action": "replace", "target_text": broken-no-quotes}\n```\n'
        '```json\n{"action": "delete", "target_text": "auto-renew"}\n```'
    )
    edits = _extract_proposed_edits(prose)
    assert len(edits) == 1
    assert edits[0]["action"] == "delete"


def test_extract_proposed_edits_skips_blocks_without_valid_action():
    """JSON blocks without a known action key are skipped."""
    from skills.legal_research import _extract_proposed_edits

    prose = '```json\n{"action": "unknown", "target_text": "X"}\n```'
    assert _extract_proposed_edits(prose) == []


def test_extract_proposed_edits_accepts_replace_all_action():
    """replace_all is a valid action — used for 'fill every X' requests."""
    from skills.legal_research import _extract_proposed_edits

    prose = (
        '```json\n{"action": "replace_all", "target_text": "Signed by: [__]", '
        '"new_text": "Signed by: John Doe"}\n```'
    )
    edits = _extract_proposed_edits(prose)
    assert len(edits) == 1
    assert edits[0]["action"] == "replace_all"


def test_chat_prompt_never_demonstrates_bundled_targets():
    """Regression guard. The doc-chat prompt must NOT show a target_text that
    stitches table columns with a tab or otherwise bundles fields — the model
    copies whatever the worked example demonstrates, and a tab-joined target
    (e.g. "Signed by: [__]\\tSigned by: Boris…") can't be located by body.search,
    so the edit fails silently with "Couldn't locate this clause."

    Commit fec4085 introduced exactly such an example; 928d462 added replace_all
    but didn't retract it, so the two contradicted and the model picked the
    concrete (broken) example. Lock the prompt against re-introducing a tab.
    """
    from skills.legal_research import CHAT_SYSTEM_PROMPT

    # The prompt teaches via JSON examples, so the danger is the *escape sequence*
    # \t / \n appearing inside an example target_text (the model copies it, the
    # parsed value becomes a real tab/newline, and body.search can't match it).
    assert "\\t" not in CHAT_SYSTEM_PROMPT      # no tab-joined two-column targets
    assert "\\n" not in CHAT_SYSTEM_PROMPT      # no newline-joined multi-row targets
    assert "replace_all" in CHAT_SYSTEM_PROMPT  # the multi-occurrence path is still taught


def test_chat_prompts_constrain_edit_scope():
    """The doc-chat prompts must tell the model to change ONLY what was asked and
    never overwrite an already-filled field. Without this, the local LLM
    over-reaches — e.g. it overwrote a completed counterparty signature block
    ("Boris Bukengolts") with the requested party's details "to ensure
    consistency", an edit the user never asked for (trace 4b24ca1d). Model-neutral
    correctness guidance, not a scenario-specific worked example."""
    from skills.legal_research import CHAT_SYSTEM_PROMPT, _JSON_RETRY_SYSTEM

    for prompt in (CHAT_SYSTEM_PROMPT, _JSON_RETRY_SYSTEM):
        low = prompt.lower()
        # Names the constraint and the "already filled" exclusion.
        assert "scope" in low
        assert "already" in low and "fill" in low
        # Does not bury it in a tab/newline-bearing example (the existing guard).
        assert "\\t" not in prompt and "\\n" not in prompt


def test_extract_proposed_edits_no_blocks_returns_empty():
    """Prose without any JSON blocks returns an empty list (Q&A turn)."""
    from skills.legal_research import _extract_proposed_edits

    assert _extract_proposed_edits("Why is the IP clause risky?") == []
    assert _extract_proposed_edits("") == []


def test_extract_proposed_edits_accepts_array_inside_one_block():
    """Regression: local LLM consolidates multi-location edits into a single
    fenced block whose body is a JSON array. The old parser expected only a
    single dict and silently dropped the array, leaving proposed_edits empty
    even though the model emitted the right structured data."""
    from skills.legal_research import _extract_proposed_edits

    prose = (
        'I will replace the placeholder in two locations.\n\n'
        '```json\n'
        '[{"action": "replace", "target_text": "Signed by: [__]", "new_text": "Signed by: John Doe"}, '
        '{"action": "replace", "target_text": "Signed by: [__]", "new_text": "Signed by: John Doe"}]\n'
        '```'
    )
    edits = _extract_proposed_edits(prose)
    assert len(edits) == 2
    assert all(e["action"] == "replace" for e in edits)
    assert all(e["new_text"] == "Signed by: John Doe" for e in edits)


def test_extract_proposed_edits_accepts_stacked_objects_in_one_block():
    """Regression (traces cea50c6b / f15f8a9b): the local LLM stacks several edit
    objects in ONE fenced block separated by newlines ({...}\\n{...}) instead of a
    JSON array. json.loads raises on multiple top-level objects, so the whole
    block was dropped -> empty edits -> the lossy JSON-retry fired and emitted a
    destructive replace_all "[__]". The parser must decode each stacked object."""
    from skills.legal_research import _extract_proposed_edits

    prose = (
        "I will update the blank fields.\n\n"
        "```json\n"
        '{"action": "replace", "target_text": "Signed by: [__]", "new_text": "Signed by: Suzy Quatro"}\n'
        '{"action": "replace", "target_text": "Title: [__]", "new_text": "Title: Chief"}\n'
        '{"action": "replace", "target_text": "for and on behalf of [__]", "new_text": "for and on behalf of Acme"}\n'
        "```"
    )
    edits = _extract_proposed_edits(prose)
    assert len(edits) == 3
    assert [e["new_text"] for e in edits] == [
        "Signed by: Suzy Quatro",
        "Title: Chief",
        "for and on behalf of Acme",
    ]


def test_extract_proposed_edits_recovers_from_unescaped_newline_in_string():
    """Local LLMs sometimes line-wrap long string values mid-content, producing
    JSON with a literal newline inside a quoted string (spec-invalid). The
    tolerant parser escapes those raw newlines and recovers the block."""
    from skills.legal_research import _extract_proposed_edits

    # Note the LITERAL newline between "Signed by:" and "[__]\\t..." inside
    # the target_text value — this is what the user saw on the second block.
    prose = (
        "I will replace the two instances.\n\n"
        "```json\n"
        '{"action": "replace", "target_text": "...long dots line...\nSigned by:\n[__]\\tSigned by: Boris", '
        '"new_text": "...long dots line...\nSigned by: John Doe\\tSigned by: Boris"}\n'
        "```"
    )
    edits = _extract_proposed_edits(prose)
    assert len(edits) == 1
    assert edits[0]["action"] == "replace"
    assert "John Doe" in edits[0]["new_text"]


def test_tolerant_json_loads_handles_internal_tabs_and_returns():
    """Tab and carriage-return characters inside string values are also escaped."""
    from skills.legal_research import _tolerant_json_loads

    raw = '{"target": "col1\tcol2\rcol3"}'  # raw \t and \r inside the string
    parsed = _tolerant_json_loads(raw)
    assert parsed is not None
    assert parsed["target"] == "col1\tcol2\rcol3"


def test_extract_proposed_edits_array_with_invalid_entries_filtered():
    """An array containing some invalid entries keeps the valid ones and drops the rest."""
    from skills.legal_research import _extract_proposed_edits

    prose = (
        '```json\n'
        '[{"action": "replace", "target_text": "X", "new_text": "Y"}, '
        '{"action": "moonwalk", "target_text": "Z"}, '
        '{"action": "insert", "anchor_text": "Sec 7", "position": "after", "new_text": "..."}]\n'
        '```'
    )
    edits = _extract_proposed_edits(prose)
    assert len(edits) == 2
    assert edits[0]["action"] == "replace"
    assert edits[1]["action"] == "insert"


# --- legal_research doc-chat fast path (no ReAct agent when uploaded_docs is present) ---


def test_legal_research_doc_chat_skips_react_agent(monkeypatch):
    """When uploaded_docs is set, the ReAct agent is NOT invoked — direct LLM only.

    The agent path on the local LLM is multi-minute (tool calls × prompt size);
    with the document already attached, the answer is single-step and tool-less.
    """
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    fake_response = MagicMock()
    fake_response.content = "Per Section 4, the cap is 12 months."
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = fake_response

    fake_agent = MagicMock()
    fake_agent.invoke.return_value = {"messages": [MagicMock(content="UNEXPECTED")]}

    with (
        patch("skills.legal_research._build_llm", return_value=fake_llm),
        patch("skills.legal_research._build_agent", return_value=fake_agent),
    ):
        from skills.legal_research import legal_research
        state = _make_state(
            request="Why is the cap risky?",
            uploaded_docs=[{"text": "Service Agreement.\n\nSection 4. Liability cap is 12 months of fees."}],
            task_type="research",
        )
        result = legal_research(state)

    fake_llm.invoke.assert_called_once()
    fake_agent.invoke.assert_not_called()
    assert "Section 4" in result["llm_response"]
    assert result["retrieved_chunks"] == []


def test_legal_research_doc_chat_extracts_edit_blocks(monkeypatch):
    """A response containing a fenced JSON block populates proposed_edits."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    fake_response = MagicMock()
    fake_response.content = (
        "Tightening the cap to 2x:\n"
        '```json\n{"action": "replace", "target_text": "12 months", "new_text": "2x"}\n```'
    )
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = fake_response

    with patch("skills.legal_research._build_llm", return_value=fake_llm):
        from skills.legal_research import legal_research
        state = _make_state(
            request="Tighten the cap to 2x.",
            uploaded_docs=[{"text": "Liability cap is 12 months."}],
        )
        result = legal_research(state)

    assert len(result["proposed_edits"]) == 1
    assert result["proposed_edits"][0]["action"] == "replace"
    assert result["proposed_edits"][0]["new_text"] == "2x"


def test_parse_json_edits_accepts_three_shapes():
    """_parse_json_edits handles the three shapes Ollama's format=json emits:
    {edits: [...]} wrapping, bare array, bare single edit."""
    from skills.legal_research import _parse_json_edits

    wrapped = '{"edits": [{"action": "replace", "target_text": "X", "new_text": "Y"}]}'
    bare_array = '[{"action": "replace", "target_text": "X", "new_text": "Y"}]'
    bare_single = '{"action": "replace", "target_text": "X", "new_text": "Y"}'

    for raw in (wrapped, bare_array, bare_single):
        edits = _parse_json_edits(raw)
        assert len(edits) == 1
        assert edits[0]["action"] == "replace"


def test_parse_json_edits_rejects_invalid_actions_and_malformed_json():
    """Malformed JSON and entries without a valid action are dropped silently."""
    from skills.legal_research import _parse_json_edits

    assert _parse_json_edits("not json at all") == []
    assert _parse_json_edits('{"edits": "not a list"}') == []
    assert _parse_json_edits('{"edits": [{"action": "frobnicate"}]}') == []
    assert _parse_json_edits('{"action": "unknown", "target_text": "X"}') == []


def test_legal_research_retries_when_edit_promise_lacks_block(monkeypatch):
    """When the LLM promises an edit in prose but emits no JSON block, the
    skill re-prompts ONCE via a separate format=json LLM and merges the
    resulting edits into state. format=json is structurally forced JSON
    output — no more 'I will replace…' hand-waving without action."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    first = MagicMock()
    first.content = (
        'I will replace the placeholder "Signed by: [__]" with "Signed by: John Doe" '
        "in two locations within the document."
    )
    retry = MagicMock()
    retry.content = (
        '{"edits": [{"action": "replace", "target_text": "Signed by: [__]\\t",'
        ' "new_text": "Signed by: John Doe\\t"}]}'
    )
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = first
    fake_json_llm = MagicMock()
    fake_json_llm.invoke.return_value = retry

    with (
        patch("skills.legal_research._build_llm", return_value=fake_llm),
        patch("skills.legal_research._build_json_llm", return_value=fake_json_llm),
    ):
        from skills.legal_research import legal_research
        state = _make_state(
            request="take every blank Signed by: [__] and fill with John Doe",
            uploaded_docs=[{"text": "Signed by: [__]\tSigned by: Boris"}],
        )
        result = legal_research(state)

    # Conversational LLM ran once; JSON-mode LLM ran once for the retry.
    fake_llm.invoke.assert_called_once()
    fake_json_llm.invoke.assert_called_once()
    # The retry's edit landed on state.
    assert len(result["proposed_edits"]) == 1
    assert result["proposed_edits"][0]["new_text"].startswith("Signed by: John Doe")


def test_legal_research_does_not_retry_when_block_already_present(monkeypatch):
    """If the first response already contains a JSON block, no retry happens."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    response = MagicMock()
    response.content = (
        "Filling the placeholder:\n"
        '```json\n{"action": "replace", "target_text": "X", "new_text": "Y"}\n```'
    )
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = response

    with patch("skills.legal_research._build_llm", return_value=fake_llm):
        from skills.legal_research import legal_research
        state = _make_state(
            request="Replace X with Y.",
            uploaded_docs=[{"text": "X here."}],
        )
        legal_research(state)

    assert fake_llm.invoke.call_count == 1


def test_edit_promise_detector_matches_past_tense():
    """The detector must match 'I have replaced...' too — without this, the
    retry path never fires when the local LLM uses past tense and the user
    sees a confident lie ('I have replaced…') with no actual edit."""
    from skills.legal_research import _looks_like_edit_promise

    # Past-tense forms — the original \breplace\b regex missed these.
    assert _looks_like_edit_promise(
        'I have replaced the placeholder "Signed by: [__]" with "Signed by: John Doe"'
    )
    assert _looks_like_edit_promise("I've inserted a force majeure clause after Section 7.")
    assert _looks_like_edit_promise("I have deleted the auto-renewal language.")
    assert _looks_like_edit_promise("I have filled all the placeholders.")

    # Present / future forms — these always worked, keep them green.
    assert _looks_like_edit_promise(
        'I will replace "Signed by: [__]" with "Signed by: John Doe".'
    )
    assert _looks_like_edit_promise("I am going to insert a clause here.")
    assert _looks_like_edit_promise("I'll delete that section.")


def test_edit_promise_detector_skips_qa_and_unrelated():
    """The detector must NOT match pure Q&A or unrelated mentions of edit verbs."""
    from skills.legal_research import _looks_like_edit_promise

    # Pure Q&A
    assert not _looks_like_edit_promise("Per Section 4, the cap is 12 months of fees.")
    assert not _looks_like_edit_promise("The IP clause is risky because it assigns ownership.")
    # Discussing concepts without claiming to do anything
    assert not _looks_like_edit_promise("Replacing the cap would require legal review.")
    assert not _looks_like_edit_promise("")
    # False-positive guards: noun forms shouldn't trigger
    assert not _looks_like_edit_promise("The replacement clause is in Section 9.")
    assert not _looks_like_edit_promise("The editor of this contract approved it.")


def test_legal_research_retries_on_past_tense_promise(monkeypatch):
    """Regression for the user-reported case: 'I have replaced...' must
    trigger the retry path. Before the regex fix this was a silent failure."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    first = MagicMock()
    first.content = (
        'I have replaced the placeholder "Signed by: [__]" with "Signed by: John Doe" '
        "in the signature block at the end of the document."
    )
    retry = MagicMock()
    retry.content = '{"edits": [{"action": "replace", "target_text": "Signed by: [__]", "new_text": "Signed by: John Doe"}]}'
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = first
    fake_json_llm = MagicMock()
    fake_json_llm.invoke.return_value = retry

    with (
        patch("skills.legal_research._build_llm", return_value=fake_llm),
        patch("skills.legal_research._build_json_llm", return_value=fake_json_llm),
    ):
        from skills.legal_research import legal_research
        state = _make_state(
            request="Fill the Signed by placeholder with John Doe.",
            uploaded_docs=[{"text": "Signed by: [__]"}],
        )
        result = legal_research(state)

    fake_llm.invoke.assert_called_once()
    fake_json_llm.invoke.assert_called_once()
    assert len(result["proposed_edits"]) == 1


def test_legal_research_does_not_retry_on_pure_qa(monkeypatch):
    """A Q&A response with no edit promise must not trigger a retry."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    response = MagicMock()
    response.content = "Section 5 caps liability at 12 months of fees. That's the standard position."
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = response

    with patch("skills.legal_research._build_llm", return_value=fake_llm):
        from skills.legal_research import legal_research
        state = _make_state(
            request="Why is the cap risky?",
            uploaded_docs=[{"text": "Liability cap: 12 months."}],
        )
        result = legal_research(state)

    assert fake_llm.invoke.call_count == 1
    assert result["proposed_edits"] == []


def test_legal_research_resets_proposed_edits_each_turn(monkeypatch):
    """A turn that produces no edit block clears the prior turn's proposal.

    Without this reset, a stale edit from the previous turn would still appear
    on the frontend, which would re-apply it (or confuse the lawyer).
    """
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    fake_response = MagicMock()
    fake_response.content = "Just answering — no edit needed."
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = fake_response

    stale_edit = [{"action": "replace", "target_text": "X", "new_text": "Y"}]
    with patch("skills.legal_research._build_llm", return_value=fake_llm):
        from skills.legal_research import legal_research
        state = _make_state(
            request="Why is X risky?",
            uploaded_docs=[{"text": "Some doc."}],
            proposed_edits=stale_edit,
        )
        result = legal_research(state)

    assert result["proposed_edits"] == []


# --- contract_review: per-type playbook bundle + type detection ---


_NDA_SAMPLE = (
    "MUTUAL NON-DISCLOSURE AGREEMENT\n\n"
    "This Mutual Non-Disclosure Agreement is entered into between ACME and Trinetix.\n"
    "Each party may disclose confidential information.\n"
)
_MSA_SAMPLE = (
    "MASTER SERVICES AGREEMENT\n\n"
    "This Master Services Agreement governs the engagement between Client and Trinetix.\n"
    "All Services shall be governed by an SOW issued under this MSA.\n"
)
_SOW_SAMPLE = (
    "STATEMENT OF WORK\n\n"
    "This Statement of Work is issued under the Master Services Agreement dated...\n"
    "Project scope: design a new web portal.\n"
)
_BAA_SAMPLE = (
    "BUSINESS ASSOCIATE AGREEMENT\n\n"
    "This BAA is entered into pursuant to HIPAA between Covered Entity and Trinetix.\n"
    "The parties handle Protected Health Information (PHI).\n"
)
# An MSA that heavily references SOWs — reproduces the real-trace mis-detect
# (the 4000-char flat count picked SOW; the title says MSA). See audit Dimension 7.
_MSA_REFERENCES_SOW_SAMPLE = (
    "MASTER SERVICE AGREEMENT\n\n"
    "This Master Service Agreement (the \"MSA\") is made by and between Client and Trinetix.\n"
    + ("Each SOW issued under this MSA defines the Services. The SOW prevails over the MSA "
       "for scope. Refer to the applicable SOW. A separate SOW or Statement of Work governs "
       "deliverables. ") * 6
)


def test_detect_contract_type_nda():
    from skills.contract_review.contract_review import _detect_contract_type
    t, ambig = _detect_contract_type(_NDA_SAMPLE)
    assert t == "nda" and not ambig


def test_detect_contract_type_msa():
    from skills.contract_review.contract_review import _detect_contract_type
    t, ambig = _detect_contract_type(_MSA_SAMPLE)
    assert t == "msa" and not ambig


def test_detect_contract_type_sow():
    from skills.contract_review.contract_review import _detect_contract_type
    t, ambig = _detect_contract_type(_SOW_SAMPLE)
    assert t == "sow" and not ambig


def test_detect_contract_type_baa():
    from skills.contract_review.contract_review import _detect_contract_type
    t, ambig = _detect_contract_type(_BAA_SAMPLE)
    assert t == "baa" and not ambig


def test_detect_contract_type_msa_with_heavy_sow_references():
    """Regression: an MSA that cites SOWs more than itself must still detect msa."""
    from skills.contract_review.contract_review import _detect_contract_type
    t, ambig = _detect_contract_type(_MSA_REFERENCES_SOW_SAMPLE)
    assert t == "msa", f"expected msa, got {t}"
    assert not ambig


def test_detect_contract_type_defaults_to_nda_on_unknown():
    """No-pattern-matches doc returns (nda, ambiguous=True)."""
    from skills.contract_review.contract_review import _detect_contract_type
    t, ambig = _detect_contract_type("Random business text without any contract keywords.")
    assert t == "nda" and ambig


def test_contract_review_sets_contract_type_detected():
    """The skill stores the detected type on state for downstream surfacing."""
    state = _make_state(
        request="Review this contract.",
        uploaded_docs=[{"text": _MSA_SAMPLE}],
        task_type="contract_review",
    )
    result = contract_review(state)
    assert result["contract_type_detected"] == "msa"


def test_contract_review_loads_per_type_bundle_in_system_prompt():
    """The system prompt is the assembled playbook bundle for the detected type.

    Verifies a representative slice of each bundle section is present and the
    per-type SKILL.md matches the detected type.
    """
    state = _make_state(
        request="Review this NDA.",
        uploaded_docs=[{"text": _NDA_SAMPLE}],
        task_type="contract_review",
    )
    result = contract_review(state)
    sys_msg = result["messages"][0]["content"]

    # Ceiling wrap
    assert sys_msg.startswith("STRICT INSTRUCTION")
    assert "PLAYBOOK END" in sys_msg
    # Global sections
    assert "# Core Contracting Principles" in sys_msg
    assert "# Risk Rating and Escalation" in sys_msg
    assert "# Approval Matrix" in sys_msg
    assert "# Required Final Output Format" in sys_msg
    assert "# AI Review Procedure" in sys_msg
    # Per-type pieces (NDA)
    assert "NDA-001" in sys_msg  # from the NDA clause matrix
    assert "# NDA Playbook Matrix" in sys_msg
    # No-signature gate language is present
    assert "DO NOT SEND FOR SIGNATURE" in sys_msg


def test_contract_review_msa_loads_msa_matrix():
    """An MSA-shaped doc loads MSA-001, not NDA-001."""
    state = _make_state(
        request="Review this MSA.",
        uploaded_docs=[{"text": _MSA_SAMPLE}],
        task_type="contract_review",
    )
    result = contract_review(state)
    sys_msg = result["messages"][0]["content"]
    assert "# MSA Playbook Matrix" in sys_msg
    assert "MSA-001" in sys_msg
    assert "# NDA Playbook Matrix" not in sys_msg


# --- contract_review: governing-MSA attachment (SOW-vs-MSA) ---

def _patch_msa(monkeypatch, value):
    """Patch get_parent_msa as imported into the contract_review module.

    We cannot use `import skills.contract_review.contract_review as m` because
    the package __init__ re-exports the `contract_review` function under that
    attribute name, masking the submodule. Use importlib to force the real
    module object, then setattr directly.
    """
    importlib.import_module("skills.contract_review.contract_review")
    _cr_module = sys.modules["skills.contract_review.contract_review"]
    monkeypatch.setattr(_cr_module, "get_parent_msa", value)


def test_contract_review_sow_attaches_governing_msa(monkeypatch):
    _patch_msa(monkeypatch, lambda client_id, **kw: ("Model MSA", "MSA LIABILITY CAP = 12 MONTHS FEES."))
    state = _make_state(
        request="Review this contract.",
        uploaded_docs=[{"text": _SOW_SAMPLE}],
        task_type="contract_review",
    )
    result = contract_review(state)

    assert result["contract_type_detected"] == "sow"
    user_msg = result["messages"][-1]["content"]
    assert "--- GOVERNING MSA (Model MSA) ---" in user_msg
    assert "MSA LIABILITY CAP = 12 MONTHS FEES." in user_msg
    # The comparison directive is the LAST system message (most-recent instruction).
    assert result["messages"][-2]["role"] == "system"
    assert "GOVERNING MSA COMPARISON" in result["messages"][-2]["content"]


def test_contract_review_sow_standalone_when_no_msa(monkeypatch):
    _patch_msa(monkeypatch, lambda client_id, **kw: None)
    state = _make_state(
        request="Review this contract.",
        uploaded_docs=[{"text": _SOW_SAMPLE}],
        task_type="contract_review",
    )
    result = contract_review(state)

    user_msg = result["messages"][-1]["content"]
    assert "GOVERNING MSA" not in user_msg
    sys_contents = [m["content"] for m in result["messages"] if m["role"] == "system"]
    assert not any("GOVERNING MSA COMPARISON" in c for c in sys_contents)
    # Standalone review still built: playbook + output constraints + user.
    assert len(result["messages"]) == 3


def test_contract_review_nda_does_not_attach_msa(monkeypatch):
    called = {"n": 0}

    def _spy(client_id, **kw):
        called["n"] += 1
        return ("X", "Y")

    _patch_msa(monkeypatch, _spy)
    state = _make_state(
        request="Review this NDA.",
        uploaded_docs=[{"text": _NDA_SAMPLE}],
        task_type="contract_review",
    )
    result = contract_review(state)

    assert called["n"] == 0  # never looked up for a non-SOW
    assert "GOVERNING MSA" not in result["messages"][-1]["content"]


def test_contract_review_truncates_oversized_msa(monkeypatch):
    big = "Z" * 30000
    _patch_msa(monkeypatch, lambda client_id, **kw: ("Big MSA", big))
    state = _make_state(
        request="Review this contract.",
        uploaded_docs=[{"text": _SOW_SAMPLE}],
        task_type="contract_review",
    )
    result = contract_review(state)

    user_msg = result["messages"][-1]["content"]
    assert "[MSA truncated to 24000 chars for review]" in user_msg
    assert ("Z" * 30000) not in user_msg  # full text not injected


def test_contract_review_msa_lookup_error_reviews_standalone(monkeypatch):
    def _boom(client_id, **kw):
        raise RuntimeError("qdrant down")

    _patch_msa(monkeypatch, _boom)
    state = _make_state(
        request="Review this contract.",
        uploaded_docs=[{"text": _SOW_SAMPLE}],
        task_type="contract_review",
    )
    result = contract_review(state)  # must NOT raise

    assert result["contract_type_detected"] == "sow"
    assert "GOVERNING MSA" not in result["messages"][-1]["content"]


def test_load_bundle_raises_on_unknown_type(tmp_path):
    from skills.base import load_bundle
    import pytest

    with pytest.raises(ValueError, match="Unknown contract_type"):
        load_bundle(tmp_path, "lease")


def test_load_bundle_raises_on_missing_bundle_file(tmp_path):
    """If the playbook directory is empty, load_bundle reports which file is missing."""
    from skills.base import load_bundle
    import pytest

    with pytest.raises(FileNotFoundError, match="Bundle file missing"):
        load_bundle(tmp_path, "nda")


def test_load_bundle_is_deterministic():
    """Repeated calls with the same inputs return byte-identical output (caching/audit)."""
    from pathlib import Path
    from skills.base import load_bundle

    playbook_root = Path(__file__).resolve().parent.parent / "skills" / "contract_review" / "playbook"
    first = load_bundle(playbook_root, "nda")
    second = load_bundle(playbook_root, "nda")
    assert first == second


def test_load_bundle_strips_generated_notices():
    """The assembled prompt must not carry the build-time HTML-comment notices
    (`<!-- GENERATED by ... -->`). They stay in the files on disk for humans but
    are noise to the LLM. Real content must survive. See docs/output_format_conflict.md."""
    from pathlib import Path
    from skills.base import load_bundle

    playbook_root = Path(__file__).resolve().parent.parent / "skills" / "contract_review" / "playbook"
    for ctype in ("nda", "msa", "sow", "baa"):
        bundle = load_bundle(playbook_root, ctype)
        assert "<!--" not in bundle, f"{ctype}: HTML comment leaked into prompt"
        assert "GENERATED by" not in bundle, f"{ctype}: generated-notice leaked into prompt"
        assert "# Review Summary" in bundle  # real content still present
        assert "No Signature" in bundle


def test_doc_chat_injects_stored_review(monkeypatch):
    import skills.legal_research as lr

    captured = {}

    class FakeResp:
        content = "Per the prior review, the IP clause is the risk."

    def fake_traced_invoke(llm, messages, name="doc_chat"):
        captured["messages"] = messages
        return FakeResp()

    monkeypatch.setattr(lr, "_build_llm", lambda: object())
    monkeypatch.setattr(lr, "traced_invoke", fake_traced_invoke)
    monkeypatch.setattr(lr, "load_latest_review",
                        lambda db_path, document_id: {"markdown": "# Review\nIP clause is risky."})

    state = _make_state(
        request="expand on the IP risk you flagged", task_type="research",
        uploaded_docs=[{"text": "MUTUAL NDA\n\nbody"}], document_id="doc-1",
    )
    lr.legal_research(state)

    contents = "\n".join(m["content"] for m in captured["messages"] if m["role"] == "system")
    assert "PRIOR REVIEW" in contents
    assert "IP clause is risky" in contents


def test_doc_chat_degrades_when_review_load_fails(monkeypatch):
    import skills.legal_research as lr

    class FakeResp:
        content = "answer"

    monkeypatch.setattr(lr, "_build_llm", lambda: object())
    monkeypatch.setattr(lr, "traced_invoke", lambda llm, messages, name="doc_chat": FakeResp())
    def _boom(db_path, document_id):
        raise RuntimeError("redis/sqlite down")
    monkeypatch.setattr(lr, "load_latest_review", _boom)

    state = _make_state(
        request="summarize", task_type="research",
        uploaded_docs=[{"text": "MUTUAL NDA\n\nbody"}], document_id="doc-1",
    )
    out = lr.legal_research(state)
    assert out["memory_degraded"] is True
    assert out["llm_response"] == "answer"   # still answers
