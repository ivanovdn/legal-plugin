# Phase 6 — Contract Generation Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the contract_generation stub with a real ReAct agent that searches case history, extracts clause patterns, and generates contracts using a local LLM — then test end-to-end with ingested CUAD contracts.

**Architecture:** `create_react_agent` from `langgraph.prebuilt` with `ChatOllama` as the model and 4 tools (search_legal, get_document, extract_clauses, escalate). The agent is wrapped in a function that bridges `LegalAgentState` ↔ agent messages. The system prompt instructs the agent to search for relevant contracts, extract clause patterns, then generate. Max 8 iterations. Output parsed into `GeneratedContract` schema.

**Tech Stack:** langgraph (create_react_agent), langchain-ollama (ChatOllama), existing RAG tools, pydantic, pytest

---

## File Structure

```
legal-plugin/
|-- skills/
|   |-- contract_generation.py   # REWRITE — ReAct agent wrapper
|   +-- schemas.py               # CREATE — GeneratedContract + other output schemas
|-- graph/
|   +-- graph.py                 # no changes — skill is still a node function
+-- tests/
    +-- test_skills.py            # CREATE — contract generation tests
```

---

### Task 1: Create skill output schemas

The `GeneratedContract` schema from the spec, plus a shared place for all skill output schemas.

**Files:**
- Create: `skills/schemas.py`

- [ ] **Step 1: Create schemas**

```python
# skills/schemas.py
"""Output schemas for all skills."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class GeneratedContract(BaseModel):
    """Output of contract_generation skill."""
    doc_type: str
    jurisdiction: str
    full_text: str
    source_contracts: list[str]     # doc_ids used as source
    extracted_patterns: dict        # clause_type -> pattern used
    deviations: list[str]           # patterns absent from history — flagged
    docx_path: str | None = None


class ClauseAnalysis(BaseModel):
    """Single clause analysis for contract review."""
    clause_type: str
    original_text: str
    risk_level: Literal["low", "medium", "high"]
    risk_reason: str
    standard_ref: str | None = None
    suggested_edit: str | None = None


class ContractReviewReport(BaseModel):
    """Output of contract_review skill."""
    contract_name: str
    jurisdiction: str
    clauses: list[ClauseAnalysis]
    summary: str
    missing_clauses: list[str]


class ComplianceCheck(BaseModel):
    """Single compliance check item."""
    rule_id: str
    rule_text: str
    source_chunk: str
    status: Literal["pass", "fail", "partial", "n/a"]
    evidence: str
    remediation: str | None = None


class ComplianceReport(BaseModel):
    """Output of compliance_check skill."""
    jurisdiction: str
    policy_scope: str
    checks: list[ComplianceCheck]
    overall: Literal["pass", "fail", "partial"]
    escalate: bool


class Citation(BaseModel):
    """Citation reference for legal research."""
    chunk_id: str
    doc_title: str
    excerpt: str


class ResearchReport(BaseModel):
    """Output of legal_research skill."""
    question: str
    answer: str
    citations: list[Citation]
    confidence: float
    conflicts: list[str]
    open_gaps: list[str]
    escalate: bool


class DraftResult(BaseModel):
    """Output of drafting skill."""
    doc_type: str
    jurisdiction: str
    full_text: str
    template_ref: str | None = None
    deviations: list[str]
    variables_filled: dict
    docx_path: str | None = None
```

- [ ] **Step 2: Commit**

```bash
git add skills/schemas.py
git commit -m "feat: add skill output schemas — GeneratedContract and all others"
```

---

### Task 2: Implement contract_generation agent

Replace the stub with a real ReAct agent. The function bridges `LegalAgentState` ↔ agent, runs the agent, and parses the result.

**Files:**
- Rewrite: `skills/contract_generation.py`
- Create: `tests/test_skills.py`

- [ ] **Step 1: Write the test**

```python
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

    # Mock the agent invocation
    fake_agent_result = {
        "messages": [
            MagicMock(content="I searched for service agreements and found relevant patterns. Here is the generated contract:\n\n**SERVICE AGREEMENT**\n\nThis Agreement is entered into..."),
        ]
    }

    with patch("skills.contract_generation._build_agent") as mock_build:
        mock_agent = MagicMock()
        mock_agent.invoke.return_value = fake_agent_result
        mock_build.return_value = mock_agent

        from skills.contract_generation import contract_generation
        state = _make_state(
            request="Generate a service agreement for Client X in Delaware jurisdiction",
            filters={"client_id": "client-x", "jurisdiction": "US-DE"},
        )
        result = contract_generation(state)

    assert result["llm_response"] != ""
    assert "[contract_generation stub]" not in result["llm_response"]


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

    assert "error" in result["llm_response"].lower() or "Error" in result["llm_response"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source .venv/bin/activate && python -m pytest tests/test_skills.py -v
```

Expected: FAIL — contract_generation still returns stub response

- [ ] **Step 3: Implement contract_generation**

```python
# skills/contract_generation.py
"""Contract generation — ReAct agent that searches case history and generates contracts."""

import logging

from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

from config import get_settings
from graph.state import LegalAgentState
from rag.tools.search_legal import search_legal
from rag.tools.get_document import get_document
from rag.tools.extract_clauses import extract_clauses
from rag.tools.escalate import escalate

logger = logging.getLogger(__name__)

CONTRACT_GEN_SYSTEM_PROMPT = """You are a contract generation agent for an internal legal team. Your job is to generate a new contract based on historical signed contracts and templates.

PROCESS:
1. Search for relevant existing contracts using search_legal (collection="legal_docs", doc_type="contract")
2. If case history is available, extract clause patterns using extract_clauses for key clause types (indemnification, termination, payment, liability, confidentiality)
3. Use get_document to retrieve full text of the most relevant source contracts
4. Generate the new contract incorporating patterns from historical contracts
5. Flag any deviations — clause types that appear in the request but have no historical pattern

RULES:
- Always filter by client_id — never use another client's contracts
- Cite every source contract by doc_id
- If you cannot find enough source material, use escalate to flag for attorney review
- The generated contract must be complete and ready for attorney review
- Use formal legal language appropriate to the jurisdiction

OUTPUT FORMAT:
After gathering all information, produce the complete contract text. Start with the contract title, then parties, recitals, and all clauses. End with signature blocks.

IMPORTANT: You are generating a DRAFT for attorney review. This will always go through human review before delivery."""


_agent_cache = {}


def _build_agent():
    """Build and cache the ReAct agent."""
    cache_key = "contract_gen"
    if cache_key in _agent_cache:
        return _agent_cache[cache_key]

    settings = get_settings()
    llm = ChatOllama(
        model=settings.llm_model,
        base_url=settings.ollama_base_url,
        temperature=0.0,
    )

    tools = [search_legal, get_document, extract_clauses, escalate]

    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=CONTRACT_GEN_SYSTEM_PROMPT,
        name="contract_generation_agent",
    )

    _agent_cache[cache_key] = agent
    return agent


def contract_generation(state: LegalAgentState) -> LegalAgentState:
    """Run the contract generation ReAct agent."""
    request = state["request"]
    filters = state.get("filters", {})
    client_id = filters.get("client_id", "internal")
    jurisdiction = filters.get("jurisdiction", "")

    context_parts = [f"Request: {request}"]
    context_parts.append(f"Client ID: {client_id}")
    if jurisdiction:
        context_parts.append(f"Jurisdiction: {jurisdiction}")

    user_message = "\n".join(context_parts)

    try:
        agent = _build_agent()
        result = agent.invoke({"messages": [{"role": "user", "content": user_message}]})

        # Extract the final response from agent messages
        messages = result.get("messages", [])
        if messages:
            # Last message is the agent's final response
            last_msg = messages[-1]
            content = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
            state["llm_response"] = content
        else:
            state["llm_response"] = "Error: Agent returned no messages."

        # Collect source doc_ids from tool calls
        source_docs = set()
        for msg in messages:
            if hasattr(msg, "content") and "doc_id:" in str(msg.content):
                # Simple extraction — real impl could parse more carefully
                import re
                ids = re.findall(r"doc_id:\s*([a-f0-9-]+)", str(msg.content))
                source_docs.update(ids)

        state["retrieved_chunks"] = [
            {"doc_id": did, "doc_title": f"Source {did[:8]}"}
            for did in source_docs
        ]

        logger.info(
            "[contract_generation] agent completed, response=%d chars, sources=%d",
            len(state["llm_response"]), len(source_docs),
        )

    except Exception as e:
        logger.error("[contract_generation] agent failed: %s", e)
        state["llm_response"] = f"Error: Contract generation agent failed — {e}"

    return state
```

- [ ] **Step 4: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_skills.py -v
```

Expected: 2 passed

- [ ] **Step 5: Run full test suite**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected: all passed (existing graph tests mock the skill, so they still work)

- [ ] **Step 6: Commit**

```bash
git add skills/contract_generation.py tests/test_skills.py
git commit -m "feat: implement contract_generation ReAct agent with tools"
```

---

### Task 3: Ingest test contracts and verify end-to-end

Ingest a few CUAD contracts with `client_id=test-client`, then submit a contract generation request via the API.

**Files:**
- Create: `scripts/test_contract_gen.py`

- [ ] **Step 1: Create test script**

```python
#!/usr/bin/env python3
"""End-to-end test: ingest contracts, then generate a new one via the agent."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main():
    from config import get_settings
    get_settings.cache_clear()

    print("=== Phase 6 End-to-End Test: Contract Generation ===\n")

    # 1. Check if contracts are already ingested
    from rag.vector_store import get_qdrant_client
    client = get_qdrant_client()
    info = client.get_collection("legal_docs")
    point_count = info.points_count
    print(f"1. legal_docs collection has {point_count} points")

    if point_count < 10:
        print("   Ingesting sample contracts...")
        from ingest.pipeline import ingest_document

        contracts_dir = Path("data/cuad/CUAD_v1/full_contract_pdf/Part_I")
        pdfs = [
            contracts_dir / "Service/ReynoldsConsumerProductsInc_20200121_S-1A_EX-10.22_11948918_EX-10.22_Service Agreement.pdf",
            contracts_dir / "Affiliate_Agreements/LinkPlusCorp_20050802_8-K_EX-10_3240252_EX-10_Affiliate Agreement.pdf",
        ]

        for pdf in pdfs:
            if pdf.exists():
                count = ingest_document(
                    filepath=pdf,
                    client_id="test-client",
                    jurisdiction="US",
                    doc_type="contract",
                    sensitivity="internal",
                    collection="legal_docs",
                )
                print(f"   Ingested {pdf.name}: {count} chunks")

    # 2. Run contract generation via graph
    print("\n2. Running contract generation agent...")
    from graph.graph import build_graph

    graph = build_graph()

    state = {
        "request": "Generate a service agreement for a software consulting engagement. Include sections for scope of work, payment terms, termination, indemnification, and confidentiality.",
        "user_id": "attorney-1",
        "uploaded_docs": [],
        "task_type": "contract_generation",
        "skill_plan": ["contract_generation"],
        "retrieval_query": "",
        "retrieved_chunks": [],
        "filters": {"client_id": "test-client", "jurisdiction": "US"},
        "messages": [],
        "llm_response": "",
        "risk_level": "",
        "risk_flags": [],
        "awaiting_review": False,
        "attorney_notes": "",
        "report": {},
        "session_id": "test-contract-gen",
        "checkpoint_ref": "",
        "trace_id": "test-contract-gen",
    }

    result = graph.invoke(state)

    print(f"\n3. Results:")
    print(f"   task_type: {result['task_type']}")
    print(f"   risk_level: {result['risk_level']}")
    print(f"   awaiting_review: {result['awaiting_review']}")
    print(f"   response length: {len(result['llm_response'])} chars")
    print(f"   sources: {len(result.get('retrieved_chunks', []))}")
    print(f"\n   First 500 chars of response:")
    print(f"   {result['llm_response'][:500]}")

    # 4. Verify constraints
    print("\n4. Constraint checks:")
    assert result["task_type"] == "contract_generation", "FAIL: wrong task_type"
    print("   [PASS] task_type = contract_generation")

    assert result["awaiting_review"] is True, "FAIL: should always await review"
    print("   [PASS] awaiting_review = True (always for contract_generation)")

    assert len(result["llm_response"]) > 100, "FAIL: response too short"
    print(f"   [PASS] response is {len(result['llm_response'])} chars")

    print("\n=== Phase 6 verification PASSED ===")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the script**

Requires Docker services running and Ollama available.

```bash
source .venv/bin/activate && python scripts/test_contract_gen.py
```

Expected: Agent searches for contracts, generates a service agreement draft, routes to human_review.

- [ ] **Step 3: Commit**

```bash
git add scripts/test_contract_gen.py
git commit -m "feat: add contract generation end-to-end test script"
```

---

## Phase 6 Exit Criteria

- [ ] `python -m pytest tests/ -v` — all tests pass
- [ ] `python scripts/test_contract_gen.py` — agent generates contract from ingested CUAD data
- [ ] Contract generation uses ReAct agent with tools (search_legal, get_document, extract_clauses, escalate)
- [ ] Agent always routes to human_review (`awaiting_review=True`)
- [ ] Agent uses `temperature=0.0`
- [ ] Agent uses `client_id` filter on all searches
