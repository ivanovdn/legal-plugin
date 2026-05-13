# Phase 8 — Remaining Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all remaining skill stubs (contract_review, compliance_check, legal_research, drafting) and implement the planner node so every capability produces structured output through the graph.

**Architecture:** Three plain skills (contract_review, compliance_check, drafting) prepare state for the shared rag_retriever + llm_caller nodes — they set `retrieval_query` and skill-specific system prompts in `messages`. One agent skill (legal_research) uses `create_react_agent` like contract_generation. The planner node uses the LLM to decompose multi-skill requests. All skills output their results as the `llm_response` string which output_formatter wraps into the report.

**Tech Stack:** langgraph, langchain-ollama, httpx, pydantic, pytest

---

## File Structure

```
legal-plugin/
|-- skills/
|   |-- contract_review.py      # REWRITE — clause analysis via LLM
|   |-- compliance_check.py     # REWRITE — policy verification via LLM
|   |-- legal_research.py       # REWRITE — ReAct agent with tools
|   +-- drafting.py             # REWRITE — template-based generation via LLM
|-- graph/nodes/
|   +-- planner.py              # REWRITE — LLM-based multi-skill decomposition
+-- tests/
    +-- test_skills.py           # MODIFY — add tests for each new skill
```

---

### Task 1: Implement contract_review skill

Plain function. Sets up a system prompt for clause analysis, enriches `retrieval_query`, and lets rag_retriever + llm_caller handle the work. The LLM is instructed to output JSON matching `ContractReviewReport`.

**Files:**
- Rewrite: `skills/contract_review.py`
- Modify: `tests/test_skills.py`

- [ ] **Step 1: Write the test**

Add to `tests/test_skills.py`:

```python
def test_contract_review_sets_prompt_and_query():
    """contract_review prepares state for rag_retriever + llm_caller."""
    from skills.contract_review import contract_review
    state = _make_state(
        request="Review the indemnification clauses in our latest NDA",
        task_type="contract_review",
        filters={"client_id": "internal"},
    )
    result = contract_review(state)

    assert result["retrieval_query"] != ""
    assert len(result["messages"]) > 0
    # System message should contain clause analysis instructions
    system_msg = result["messages"][0]
    assert system_msg["role"] == "system"
    assert "clause" in system_msg["content"].lower()
```

- [ ] **Step 2: Implement contract_review**

```python
# skills/contract_review.py
"""Contract review — clause extraction and risk analysis."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a contract review specialist. Analyze the provided contract text and identify all clauses.

For each clause, provide:
- clause_type: the category (e.g., indemnification, termination, payment, confidentiality, liability, force_majeure, governing_law, dispute_resolution)
- original_text: the exact clause text
- risk_level: "low", "medium", or "high"
- risk_reason: why this risk level was assigned
- standard_ref: reference to any standard or regulation this clause relates to (or null)
- suggested_edit: suggested improvement if risk is medium or high (or null)

Also identify any missing clauses that should typically be present.

Cite every source document by doc_id and doc_title. If context is insufficient, say so.

Respond with your analysis as structured text with clear sections for each clause."""


def contract_review(state: LegalAgentState) -> LegalAgentState:
    """Prepare state for clause analysis via rag_retriever + llm_caller."""
    request = state["request"]

    state["retrieval_query"] = request
    state["messages"] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": request},
    ]

    logger.info("[contract_review] prepared for clause analysis: %s", request[:80])
    return state
```

- [ ] **Step 3: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_skills.py -v
```

Expected: 4 passed (3 existing + 1 new)

- [ ] **Step 4: Commit**

```bash
git add skills/contract_review.py tests/test_skills.py
git commit -m "feat: implement contract_review skill — clause analysis"
```

---

### Task 2: Implement compliance_check skill

Plain function. System prompt instructs LLM to check document against policies/regulations.

**Files:**
- Rewrite: `skills/compliance_check.py`
- Modify: `tests/test_skills.py`

- [ ] **Step 1: Write the test**

Add to `tests/test_skills.py`:

```python
def test_compliance_check_sets_prompt_and_query():
    """compliance_check prepares state for policy verification."""
    from skills.compliance_check import compliance_check
    state = _make_state(
        request="Check if our data retention policy complies with GDPR",
        task_type="compliance",
        filters={"client_id": "internal"},
    )
    result = compliance_check(state)

    assert result["retrieval_query"] != ""
    assert len(result["messages"]) > 0
    system_msg = result["messages"][0]
    assert system_msg["role"] == "system"
    assert "compliance" in system_msg["content"].lower()
```

- [ ] **Step 2: Implement compliance_check**

```python
# skills/compliance_check.py
"""Compliance check — policy/regulation verification."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a compliance verification specialist. Check the provided documents against applicable policies, regulations, and jurisdiction rules.

For each compliance check, provide:
- rule_id: identifier for the rule or regulation being checked
- rule_text: the text of the rule
- source_chunk: the relevant document text being checked
- status: "pass", "fail", "partial", or "n/a"
- evidence: specific evidence from the document supporting the status
- remediation: suggested fix if status is "fail" or "partial" (or null)

Determine the overall compliance status (pass, fail, or partial).
If any check has high severity or you are uncertain, set escalate to true.

Cite every source document by doc_id and doc_title. If context is insufficient, say so.

Respond with your analysis as structured text with clear sections for each check."""


def compliance_check(state: LegalAgentState) -> LegalAgentState:
    """Prepare state for compliance verification via rag_retriever + llm_caller."""
    request = state["request"]

    state["retrieval_query"] = request
    state["messages"] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": request},
    ]

    logger.info("[compliance_check] prepared for compliance verification: %s", request[:80])
    return state
```

- [ ] **Step 3: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_skills.py -v
```

Expected: 5 passed

- [ ] **Step 4: Commit**

```bash
git add skills/compliance_check.py tests/test_skills.py
git commit -m "feat: implement compliance_check skill — policy verification"
```

---

### Task 3: Implement drafting skill

Plain function. Always routes to human_review (enforced by graph routing, not by this function). System prompt instructs LLM to fill templates and produce formal documents.

**Files:**
- Rewrite: `skills/drafting.py`
- Modify: `tests/test_skills.py`

- [ ] **Step 1: Write the test**

Add to `tests/test_skills.py`:

```python
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
    system_msg = result["messages"][0]
    assert system_msg["role"] == "system"
    assert "draft" in system_msg["content"].lower()
```

- [ ] **Step 2: Implement drafting**

```python
# skills/drafting.py
"""Drafting — template-based document generation."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a legal document drafting specialist. Generate formal legal documents based on templates and the provided context.

Your task:
1. Search for relevant templates or similar documents in the knowledge base
2. Use them as the basis for the new document
3. Fill in all required variables (parties, dates, terms, jurisdiction)
4. Flag any deviations from standard templates

The output should be a complete, ready-to-review legal document with:
- Document title and type
- All standard sections for the document type
- Proper legal language appropriate to the jurisdiction
- Signature blocks

Cite every source template or document by doc_id and doc_title.
If no suitable template is found, generate the document from best practices and flag this as a deviation.

IMPORTANT: This is a DRAFT for attorney review. It will always go through human review before delivery."""


def drafting(state: LegalAgentState) -> LegalAgentState:
    """Prepare state for document drafting via rag_retriever + llm_caller."""
    request = state["request"]
    filters = state.get("filters", {})

    # Search for templates specifically
    query_parts = [request]
    if filters.get("jurisdiction"):
        query_parts.append(f"jurisdiction: {filters['jurisdiction']}")
    state["retrieval_query"] = " ".join(query_parts)

    state["messages"] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": request},
    ]

    logger.info("[drafting] prepared for document drafting: %s", request[:80])
    return state
```

- [ ] **Step 3: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_skills.py -v
```

Expected: 6 passed

- [ ] **Step 4: Commit**

```bash
git add skills/drafting.py tests/test_skills.py
git commit -m "feat: implement drafting skill — template-based document generation"
```

---

### Task 4: Implement legal_research agent

ReAct agent like contract_generation. Uses search_legal, get_document, and escalate tools. Multi-hop retrieval for answering legal questions.

**Files:**
- Rewrite: `skills/legal_research.py`
- Modify: `tests/test_skills.py`

- [ ] **Step 1: Write the test**

Add to `tests/test_skills.py`:

```python
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
            filters={"client_id": "internal"},
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
```

- [ ] **Step 2: Implement legal_research**

```python
# skills/legal_research.py
"""Legal research — multi-hop retrieval ReAct agent."""

import logging
import re

from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

from config import get_settings
from graph.state import LegalAgentState
from rag.tools.search_legal import search_legal
from rag.tools.get_document import get_document
from rag.tools.escalate import escalate

logger = logging.getLogger(__name__)

RESEARCH_SYSTEM_PROMPT = """You are a legal research agent for an internal legal team. Your job is to answer legal questions by searching the knowledge base.

PROCESS:
1. Search for relevant documents using search_legal with appropriate filters
2. If a result looks promising, use get_document to get the full text
3. Perform multiple searches with different query formulations if initial results are insufficient
4. Synthesize findings into a clear, well-cited answer
5. If you cannot find sufficient information, use escalate

RULES:
- Always filter by client_id — never access another client's documents
- Cite every claim with doc_id and doc_title
- If sources conflict, note the conflict explicitly
- If gaps remain in the answer, list them as open questions
- Be precise about what the sources say vs. your interpretation

OUTPUT:
Provide a comprehensive answer with:
- Direct answer to the question
- Supporting citations from retrieved documents
- Any conflicts between sources
- Open gaps that need further research
- Confidence assessment (how well-supported is the answer)"""


_agent_cache = {}


def _build_agent():
    """Build and cache the ReAct agent."""
    cache_key = "legal_research"
    if cache_key in _agent_cache:
        return _agent_cache[cache_key]

    settings = get_settings()
    llm = ChatOllama(
        model=settings.llm_model,
        base_url=settings.ollama_base_url,
        temperature=0.0,
    )

    tools = [search_legal, get_document, escalate]

    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=RESEARCH_SYSTEM_PROMPT,
        name="legal_research_agent",
    )

    _agent_cache[cache_key] = agent
    return agent


def legal_research(state: LegalAgentState) -> LegalAgentState:
    """Run the legal research ReAct agent."""
    request = state["request"]
    filters = state.get("filters", {})
    client_id = filters.get("client_id", "internal")

    context_parts = [f"Question: {request}", f"Client ID: {client_id}"]
    if filters.get("jurisdiction"):
        context_parts.append(f"Jurisdiction: {filters['jurisdiction']}")

    user_message = "\n".join(context_parts)

    try:
        agent = _build_agent()
        result = agent.invoke({"messages": [{"role": "user", "content": user_message}]})

        messages = result.get("messages", [])
        if messages:
            last_msg = messages[-1]
            content = last_msg.content if hasattr(last_msg, "content") else str(last_msg)
            state["llm_response"] = content
        else:
            state["llm_response"] = "Error: Agent returned no messages."

        # Collect source doc_ids
        source_docs = set()
        for msg in messages:
            msg_text = msg.content if hasattr(msg, "content") else str(msg)
            ids = re.findall(r"doc_id:\s*([a-f0-9-]+)", msg_text)
            source_docs.update(ids)

        state["retrieved_chunks"] = [
            {"doc_id": did, "doc_title": f"Source {did[:8]}"}
            for did in source_docs
        ]

        logger.info(
            "[legal_research] agent completed, response=%d chars, sources=%d",
            len(state["llm_response"]), len(source_docs),
        )

    except Exception as e:
        logger.error("[legal_research] agent failed: %s", e)
        state["llm_response"] = f"Error: Legal research agent failed — {e}"

    return state
```

- [ ] **Step 3: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_skills.py -v
```

Expected: 8 passed

- [ ] **Step 4: Commit**

```bash
git add skills/legal_research.py tests/test_skills.py
git commit -m "feat: implement legal_research ReAct agent with tools"
```

---

### Task 5: Implement planner node

Replaces the stub. Calls the LLM to decompose multi-skill requests into an ordered skill_plan. For single-skill requests (most common), it's never called (routing skips it).

**Files:**
- Rewrite: `graph/nodes/planner.py`
- Modify: `tests/test_nodes.py`

- [ ] **Step 1: Write the test**

Add to `tests/test_nodes.py`:

```python
def test_planner_decomposes_multi_skill(monkeypatch):
    """planner calls LLM to break down multi-skill requests."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "message": {"content": '{"skill_plan": ["contract_review", "compliance"], "task_type": "contract_review"}'}
    }

    with patch("graph.nodes.planner.httpx.post", return_value=fake_response):
        from graph.nodes.planner import planner
        state = _make_state(
            request="Review this contract and check compliance with GDPR",
            skill_plan=["contract_review", "compliance"],
            task_type="multi",
        )
        result = planner(state)

    # Planner should set task_type to the first skill in the plan
    assert result["task_type"] in ("contract_review", "compliance")
    assert len(result["skill_plan"]) >= 1


def test_planner_keeps_single_skill():
    """planner passes through when skill_plan has only one skill."""
    from graph.nodes.planner import planner
    state = _make_state(
        request="Review this contract",
        skill_plan=["contract_review"],
        task_type="contract_review",
    )
    result = planner(state)
    assert result["task_type"] == "contract_review"
    assert result["skill_plan"] == ["contract_review"]
```

- [ ] **Step 2: Implement planner**

```python
# graph/nodes/planner.py
"""Planner — breaks multi-skill requests into ordered skill_plan."""

import json
import logging

import httpx

from config import get_settings
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)

_PLANNER_PROMPT = """You are a legal task planner. Given a user request that requires multiple legal skills, determine the optimal execution order.

Available skills:
- contract_review: Review and analyze contract clauses
- compliance: Check documents against policies and regulations
- contract_generation: Generate new contracts
- research: Answer legal questions from knowledge base
- drafting: Generate legal documents from templates

The user's request: {request}

Current skill plan: {skill_plan}

Determine which skill should execute FIRST (the most important one for this request).
Respond with JSON: {{"task_type": "<first_skill_to_execute>", "skill_plan": ["<ordered_list>"]}}"""


def planner(state: LegalAgentState) -> LegalAgentState:
    """Decompose multi-skill requests. Sets task_type to first skill to execute."""
    skill_plan = state.get("skill_plan", [])

    # Single skill — nothing to plan
    if len(skill_plan) <= 1:
        logger.info("[planner] single skill, no decomposition needed")
        return state

    settings = get_settings()

    try:
        response = httpx.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.llm_model,
                "messages": [
                    {"role": "user", "content": _PLANNER_PROMPT.format(
                        request=state["request"],
                        skill_plan=skill_plan,
                    )}
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.0},
            },
            timeout=30.0,
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        parsed = json.loads(content)

        if "task_type" in parsed:
            state["task_type"] = parsed["task_type"]
        if "skill_plan" in parsed:
            state["skill_plan"] = parsed["skill_plan"]

        logger.info("[planner] decomposed: task_type=%s, plan=%s", state["task_type"], state["skill_plan"])

    except Exception as e:
        logger.warning("[planner] LLM planning failed: %s — using first skill in plan", e)
        state["task_type"] = skill_plan[0]

    return state
```

- [ ] **Step 3: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_nodes.py -v
```

Expected: 17 passed (15 existing + 2 new)

- [ ] **Step 4: Commit**

```bash
git add graph/nodes/planner.py tests/test_nodes.py
git commit -m "feat: implement planner node — LLM-based multi-skill decomposition"
```

---

### Task 6: Update llm_caller to use skill-provided messages

Currently llm_caller builds its own system prompt. Skills like contract_review now set `messages` in state. The llm_caller should use skill-provided messages when available, falling back to its default prompt.

**Files:**
- Modify: `graph/nodes/llm_caller.py`
- Modify: `tests/test_nodes.py`

- [ ] **Step 1: Write the test**

Add to `tests/test_nodes.py`:

```python
def test_llm_caller_uses_skill_messages(monkeypatch):
    """llm_caller uses messages from state when set by a skill."""
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    from config import get_settings
    get_settings.cache_clear()

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "message": {"content": "Clause analysis: The indemnification clause is standard."}
    }

    with patch("graph.nodes.llm_caller.httpx.post", return_value=fake_response) as mock_post:
        from graph.nodes.llm_caller import llm_caller
        state = _make_state(
            request="Review clauses",
            messages=[
                {"role": "system", "content": "You are a contract review specialist."},
                {"role": "user", "content": "Review clauses"},
            ],
            retrieved_chunks=[
                {"chunk_id": "c1", "doc_id": "d1", "doc_title": "Contract A", "text": "Indemnity clause text"},
            ],
        )
        result = llm_caller(state)

    call_body = mock_post.call_args[1]["json"]
    # Should use the skill-provided system message, not the default
    assert call_body["messages"][0]["content"] == "You are a contract review specialist."
```

- [ ] **Step 2: Update llm_caller**

Modify `graph/nodes/llm_caller.py` to check if `state["messages"]` is populated:

```python
# graph/nodes/llm_caller.py
"""LLM caller — sends prompt + retrieved context to Ollama."""

import logging

import httpx

from config import get_settings
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = """You are a legal assistant for an internal legal team. Answer the user's request using ONLY the provided context. For every claim, cite the source document (doc_title and doc_id). If the context is insufficient, say so explicitly — do not fabricate information."""


def _build_context(chunks: list[dict]) -> str:
    """Format retrieved chunks as numbered context."""
    if not chunks:
        return "No documents retrieved."
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(
            f"[{i}] {c.get('doc_title', 'Unknown')} (doc_id: {c.get('doc_id', '?')})\n"
            f"{c.get('text', '')}"
        )
    return "\n\n---\n\n".join(parts)


def llm_caller(state: LegalAgentState) -> LegalAgentState:
    """Call Ollama with context + request. temperature=0.0 always."""
    settings = get_settings()
    chunks = state.get("retrieved_chunks", [])
    context = _build_context(chunks)

    # Use skill-provided messages if available, otherwise build default
    skill_messages = state.get("messages", [])
    if skill_messages:
        messages = list(skill_messages)
        # Inject context into the last user message
        if messages and messages[-1]["role"] == "user":
            messages[-1] = {
                "role": "user",
                "content": f"Context:\n{context}\n\n{messages[-1]['content']}",
            }
    else:
        messages = [
            {"role": "system", "content": _DEFAULT_SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n{context}\n\nRequest: {state['request']}"},
        ]

    try:
        response = httpx.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.llm_model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.0},
            },
            timeout=120.0,
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        state["llm_response"] = content
        logger.info("[llm_caller] got %d char response", len(content))
    except Exception as e:
        logger.error("[llm_caller] LLM call failed: %s", e)
        state["llm_response"] = f"Error: LLM call failed — {e}"

    return state
```

- [ ] **Step 3: Run all tests**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected: all passed

- [ ] **Step 4: Commit**

```bash
git add graph/nodes/llm_caller.py tests/test_nodes.py
git commit -m "feat: llm_caller supports skill-provided messages"
```

---

### Task 7: Full test suite and verification

Run full suite, verify all skills produce output through the graph.

- [ ] **Step 1: Run full test suite**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected: all passed

- [ ] **Step 2: Verify graph flow for each skill type via script**

Update `scripts/test_graph_flow.py` or run inline:

```bash
source .venv/bin/activate && python -c "
from unittest.mock import patch, MagicMock

def fake_ollama(url, **kwargs):
    r = MagicMock()
    r.status_code = 200
    body = kwargs.get('json', {})
    msgs = body.get('messages', [])
    user = msgs[-1]['content'] if msgs else ''
    if 'task type' in user.lower() or 'classify' in user.lower():
        r.json.return_value = {'message': {'content': '{\"task_type\": \"research\"}'}}
    elif 'planner' in user.lower() or 'skill_plan' in user.lower():
        r.json.return_value = {'message': {'content': '{\"task_type\": \"contract_review\", \"skill_plan\": [\"contract_review\"]}'}}
    else:
        r.json.return_value = {'message': {'content': 'Based on Contract A (doc_id: d1), the analysis shows...'}}
    return r

fake_chunks = [{'chunk_id': 'c1', 'doc_id': 'd1', 'doc_title': 'Contract A',
    'text': 'text', 'rrf_score': 0.8, 'doc_type': 'contract',
    'client_id': 'internal', 'jurisdiction': 'US'}]

import graph.nodes.memory_writer as mw
mw._db_initialized = False

with patch('graph.nodes.intent_router.httpx.post', side_effect=fake_ollama), \
     patch('graph.nodes.llm_caller.httpx.post', side_effect=fake_ollama), \
     patch('graph.nodes.planner.httpx.post', side_effect=fake_ollama), \
     patch('graph.nodes.rag_retriever.hybrid_search', return_value=fake_chunks):
    from graph.graph import build_graph
    graph = build_graph()
    for tt in ['contract_review', 'compliance', 'drafting']:
        r = graph.invoke({
            'request': f'Test {tt}', 'user_id': 'a1', 'uploaded_docs': [],
            'task_type': tt, 'skill_plan': [tt], 'retrieval_query': '',
            'retrieved_chunks': [], 'filters': {}, 'messages': [],
            'llm_response': '', 'risk_level': '', 'risk_flags': [],
            'awaiting_review': False, 'attorney_notes': '', 'report': {},
            'session_id': f'test-{tt}', 'checkpoint_ref': '', 'trace_id': '',
        })
        review = '(human_review)' if r['awaiting_review'] else '(auto)'
        print(f'{tt}: task_type={r[\"task_type\"]}, risk={r[\"risk_level\"]}, review={r[\"awaiting_review\"]} {review}')
print('All skills verified.')
"
```

Expected: Each skill type flows through graph, drafting shows awaiting_review=True.

- [ ] **Step 3: Commit any remaining changes**

```bash
git add -A && git commit -m "feat: Phase 8 complete — all skills implemented"
```

---

## Phase 8 Exit Criteria

- [ ] `python -m pytest tests/ -v` — all tests pass
- [ ] contract_review: sets system prompt for clause analysis, flows through graph
- [ ] compliance_check: sets system prompt for policy verification, flows through graph
- [ ] drafting: sets system prompt for document generation, always routes to human_review
- [ ] legal_research: ReAct agent with search_legal, get_document, escalate tools
- [ ] planner: LLM decomposes multi-skill requests, falls back to first skill on error
- [ ] llm_caller: uses skill-provided messages when available
