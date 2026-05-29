# tests/test_skills.py
"""Tests for skill implementations."""

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


def test_extract_proposed_edits_no_blocks_returns_empty():
    """Prose without any JSON blocks returns an empty list (Q&A turn)."""
    from skills.legal_research import _extract_proposed_edits

    assert _extract_proposed_edits("Why is the IP clause risky?") == []
    assert _extract_proposed_edits("") == []


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
