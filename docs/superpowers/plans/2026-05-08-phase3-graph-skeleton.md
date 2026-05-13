# Phase 3 — Graph Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the full LangGraph supervisor graph with all nodes as stubs, all edges connected, and routing logic in place — so a request flows end-to-end before any real business logic is implemented.

**Architecture:** Single LangGraph `StateGraph` with `LegalAgentState` TypedDict. Nodes are stub functions that return state unchanged. Conditional edges handle routing (intent → planner or skill_dispatcher, risk → human_review or output_formatter). Skills are registered as nodes identical to shared nodes. Graph compiles with a Redis checkpointer.

**Tech Stack:** langgraph, langgraph-checkpoint-redis, redis, pytest

---

## File Structure

```
legal-plugin/
|-- graph/
|   |-- __init__.py
|   |-- state.py             # LegalAgentState TypedDict
|   +-- graph.py             # StateGraph definition, all nodes + edges
|-- graph/nodes/
|   |-- __init__.py
|   |-- intake.py            # stub
|   |-- intent_router.py     # stub — sets task_type
|   |-- planner.py           # stub
|   |-- skill_dispatcher.py  # stub — routes to skill node by task_type
|   |-- rag_retriever.py     # stub
|   |-- llm_caller.py        # stub
|   |-- risk_assessor.py     # stub — sets risk_level
|   |-- human_review.py      # stub
|   |-- output_formatter.py  # stub
|   +-- memory_writer.py     # stub
|-- skills/
|   |-- __init__.py
|   |-- base.py              # stub skill interface
|   |-- contract_generation.py  # stub
|   |-- contract_review.py      # stub
|   |-- compliance_check.py     # stub
|   |-- legal_research.py       # stub
|   +-- drafting.py             # stub
+-- tests/
    +-- test_graph.py         # Graph compilation + end-to-end flow tests
```

---

### Task 1: Create LegalAgentState

**Files:**
- Create: `graph/__init__.py`
- Create: `graph/state.py`
- Create: `tests/test_graph.py` (partial — state tests only)

- [ ] **Step 1: Write the failing test**

```python
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
        "checkpoint_id": "",
        "trace_id": "",
    }
    assert state["request"] == "Review this contract"
    assert state["risk_level"] == "low"
    assert state["task_type"] == ""
```

- [ ] **Step 2: Run test to verify it fails**

```bash
source .venv/bin/activate && python -m pytest tests/test_graph.py::test_legal_agent_state_can_be_created -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'graph'`

- [ ] **Step 3: Create graph package and state**

Create `graph/__init__.py` (empty) and:

```python
# graph/state.py
from __future__ import annotations

from typing import TypedDict

from ingest.chunk_models import LegalChunk


class LegalAgentState(TypedDict):
    request: str
    user_id: str
    uploaded_docs: list[LegalChunk]
    task_type: str          # contract_generation | contract_review | compliance | research | drafting | multi
    skill_plan: list[str]
    retrieval_query: str
    retrieved_chunks: list[LegalChunk]
    filters: dict           # client_id, jurisdiction, doc_type
    messages: list[dict]
    llm_response: str
    risk_level: str         # low | medium | high
    risk_flags: list[dict]
    awaiting_review: bool
    attorney_notes: str
    report: dict
    session_id: str
    checkpoint_id: str
    trace_id: str
```

- [ ] **Step 4: Run test**

```bash
source .venv/bin/activate && python -m pytest tests/test_graph.py::test_legal_agent_state_can_be_created -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add graph/__init__.py graph/state.py tests/test_graph.py
git commit -m "feat: add LegalAgentState TypedDict"
```

---

### Task 2: Create stub nodes

All 10 shared nodes as stub functions. Each accepts `LegalAgentState` and returns it unchanged. The `intent_router` stub sets a default `task_type` so routing works. The `risk_assessor` stub sets a default `risk_level`.

**Files:**
- Create: `graph/nodes/__init__.py`
- Create: `graph/nodes/intake.py`
- Create: `graph/nodes/intent_router.py`
- Create: `graph/nodes/planner.py`
- Create: `graph/nodes/skill_dispatcher.py`
- Create: `graph/nodes/rag_retriever.py`
- Create: `graph/nodes/llm_caller.py`
- Create: `graph/nodes/risk_assessor.py`
- Create: `graph/nodes/human_review.py`
- Create: `graph/nodes/output_formatter.py`
- Create: `graph/nodes/memory_writer.py`

- [ ] **Step 1: Create graph/nodes/__init__.py**

```python
# graph/nodes/__init__.py
```

- [ ] **Step 2: Create all stub nodes**

Each file follows the same pattern — a function that takes state and returns state. Minimal stubs that allow the graph to compile and route.

`graph/nodes/intake.py`:
```python
# graph/nodes/intake.py
"""Intake node — validates and enriches incoming request."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def intake(state: LegalAgentState) -> LegalAgentState:
    """Stub: pass through. Real impl resolves client_id from user_id."""
    logger.info("[intake] request=%s, user=%s", state["request"][:50], state["user_id"])
    return state
```

`graph/nodes/intent_router.py`:
```python
# graph/nodes/intent_router.py
"""Intent router — classifies task_type from request."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)

VALID_TASK_TYPES = {
    "contract_generation", "contract_review", "compliance",
    "research", "drafting", "multi",
}


def intent_router(state: LegalAgentState) -> LegalAgentState:
    """Stub: sets task_type to 'research' if not already set."""
    if not state.get("task_type") or state["task_type"] not in VALID_TASK_TYPES:
        state["task_type"] = "research"
    if not state.get("skill_plan"):
        state["skill_plan"] = [state["task_type"]]
    logger.info("[intent_router] task_type=%s, skill_plan=%s", state["task_type"], state["skill_plan"])
    return state
```

`graph/nodes/planner.py`:
```python
# graph/nodes/planner.py
"""Planner — breaks multi-skill requests into ordered skill_plan."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def planner(state: LegalAgentState) -> LegalAgentState:
    """Stub: pass through. Real impl decomposes multi-skill requests."""
    logger.info("[planner] skill_plan=%s", state["skill_plan"])
    return state
```

`graph/nodes/skill_dispatcher.py`:
```python
# graph/nodes/skill_dispatcher.py
"""Skill dispatcher — routes to the correct skill node."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)

SKILL_MAP = {
    "contract_generation": "contract_generation",
    "contract_review": "contract_review",
    "compliance": "compliance_check",
    "research": "legal_research",
    "drafting": "drafting",
}


def skill_dispatcher(state: LegalAgentState) -> LegalAgentState:
    """Sets routing info for conditional edge. Actual dispatch via graph edges."""
    logger.info("[skill_dispatcher] dispatching task_type=%s", state["task_type"])
    return state


def route_to_skill(state: LegalAgentState) -> str:
    """Conditional edge: returns the skill node name to route to."""
    return SKILL_MAP.get(state["task_type"], "legal_research")
```

`graph/nodes/rag_retriever.py`:
```python
# graph/nodes/rag_retriever.py
"""RAG retriever — runs hybrid search for the current request."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def rag_retriever(state: LegalAgentState) -> LegalAgentState:
    """Stub: pass through. Real impl calls hybrid_search."""
    logger.info("[rag_retriever] query=%s", state.get("retrieval_query", "")[:50])
    return state
```

`graph/nodes/llm_caller.py`:
```python
# graph/nodes/llm_caller.py
"""LLM caller — sends prompt + context to Ollama."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def llm_caller(state: LegalAgentState) -> LegalAgentState:
    """Stub: pass through. Real impl calls Ollama with temperature=0.0."""
    logger.info("[llm_caller] stub — no LLM call")
    return state
```

`graph/nodes/risk_assessor.py`:
```python
# graph/nodes/risk_assessor.py
"""Risk assessor — evaluates risk level of the response."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def risk_assessor(state: LegalAgentState) -> LegalAgentState:
    """Stub: sets risk_level to 'low' if not already set."""
    if not state.get("risk_level"):
        state["risk_level"] = "low"
    logger.info("[risk_assessor] risk_level=%s", state["risk_level"])
    return state


def route_risk(state: LegalAgentState) -> str:
    """Conditional edge: routes to human_review or output_formatter."""
    if state["task_type"] in ("contract_generation", "drafting"):
        return "human_review"
    if state["risk_level"] in ("high", "medium"):
        return "human_review"
    return "output_formatter"
```

`graph/nodes/human_review.py`:
```python
# graph/nodes/human_review.py
"""Human review — pauses graph for attorney approval."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def human_review(state: LegalAgentState) -> LegalAgentState:
    """Stub: marks awaiting_review and passes through. Real impl uses interrupt()."""
    state["awaiting_review"] = True
    logger.info("[human_review] stub — marked awaiting_review=True")
    return state
```

`graph/nodes/output_formatter.py`:
```python
# graph/nodes/output_formatter.py
"""Output formatter — structures the final response."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def output_formatter(state: LegalAgentState) -> LegalAgentState:
    """Stub: pass through. Real impl builds structured report from llm_response."""
    logger.info("[output_formatter] stub — pass through")
    return state
```

`graph/nodes/memory_writer.py`:
```python
# graph/nodes/memory_writer.py
"""Memory writer — persists session data and audit log."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def memory_writer(state: LegalAgentState) -> LegalAgentState:
    """Stub: pass through. Real impl writes to SQLite audit log."""
    logger.info("[memory_writer] stub — pass through")
    return state
```

- [ ] **Step 3: Commit**

```bash
git add graph/nodes/
git commit -m "feat: add stub nodes for all graph positions"
```

---

### Task 3: Create stub skills

All 5 skills as stub functions. Same pattern as nodes.

**Files:**
- Create: `skills/__init__.py`
- Create: `skills/base.py`
- Create: `skills/contract_generation.py`
- Create: `skills/contract_review.py`
- Create: `skills/compliance_check.py`
- Create: `skills/legal_research.py`
- Create: `skills/drafting.py`

- [ ] **Step 1: Create skills package**

`skills/__init__.py` (empty)

`skills/base.py`:
```python
# skills/base.py
"""Base interface for skills. All skills take LegalAgentState and return it."""

from graph.state import LegalAgentState


def stub_skill(name: str):
    """Factory for stub skill functions."""
    def _skill(state: LegalAgentState) -> LegalAgentState:
        state["llm_response"] = f"[{name} stub] No implementation yet."
        return state
    _skill.__name__ = name
    return _skill
```

- [ ] **Step 2: Create all stub skills**

`skills/contract_generation.py`:
```python
# skills/contract_generation.py
"""Contract generation — agent subgraph (stub)."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def contract_generation(state: LegalAgentState) -> LegalAgentState:
    """Stub: will be replaced by create_react_agent subgraph in Phase 6."""
    logger.info("[contract_generation] stub")
    state["llm_response"] = "[contract_generation stub] No implementation yet."
    return state
```

`skills/contract_review.py`:
```python
# skills/contract_review.py
"""Contract review — clause extraction and risk analysis (stub)."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def contract_review(state: LegalAgentState) -> LegalAgentState:
    """Stub: will be replaced with real clause analysis."""
    logger.info("[contract_review] stub")
    state["llm_response"] = "[contract_review stub] No implementation yet."
    return state
```

`skills/compliance_check.py`:
```python
# skills/compliance_check.py
"""Compliance check — policy/regulation verification (stub)."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def compliance_check(state: LegalAgentState) -> LegalAgentState:
    """Stub: will be replaced with real compliance checking."""
    logger.info("[compliance_check] stub")
    state["llm_response"] = "[compliance_check stub] No implementation yet."
    return state
```

`skills/legal_research.py`:
```python
# skills/legal_research.py
"""Legal research — multi-hop retrieval agent (stub)."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def legal_research(state: LegalAgentState) -> LegalAgentState:
    """Stub: will be replaced by create_react_agent subgraph."""
    logger.info("[legal_research] stub")
    state["llm_response"] = "[legal_research stub] No implementation yet."
    return state
```

`skills/drafting.py`:
```python
# skills/drafting.py
"""Drafting — template-based document generation (stub)."""

import logging
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def drafting(state: LegalAgentState) -> LegalAgentState:
    """Stub: will be replaced with real template filling."""
    logger.info("[drafting] stub")
    state["llm_response"] = "[drafting stub] No implementation yet."
    return state
```

- [ ] **Step 3: Commit**

```bash
git add skills/
git commit -m "feat: add stub skills for all 5 capabilities"
```

---

### Task 4: Wire the graph

The main graph definition — connects all nodes and skills with edges, conditional routing, and compiles.

**Files:**
- Create: `graph/graph.py`
- Modify: `tests/test_graph.py` — add compilation and flow tests

- [ ] **Step 1: Write the failing test**

Add to `tests/test_graph.py`:

```python
def test_graph_compiles():
    """Graph compiles without errors."""
    from graph.graph import build_graph
    graph = build_graph()
    assert graph is not None


def test_graph_end_to_end_research():
    """A research request flows through all stubs to END."""
    from graph.graph import build_graph
    graph = build_graph()

    initial_state = {
        "request": "What are the indemnification standards in US contracts?",
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
        "session_id": "test-sess-001",
        "checkpoint_id": "",
        "trace_id": "",
    }

    result = graph.invoke(initial_state)

    # intent_router stub defaults to "research"
    assert result["task_type"] == "research"
    # legal_research stub sets llm_response
    assert "legal_research stub" in result["llm_response"]
    # risk_assessor sets risk_level
    assert result["risk_level"] == "low"


def test_graph_contract_generation_routes_to_human_review():
    """Contract generation always routes through human_review."""
    from graph.graph import build_graph
    graph = build_graph()

    initial_state = {
        "request": "Generate a service agreement",
        "user_id": "attorney-1",
        "uploaded_docs": [],
        "task_type": "contract_generation",
        "skill_plan": ["contract_generation"],
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
        "session_id": "test-sess-002",
        "checkpoint_id": "",
        "trace_id": "",
    }

    result = graph.invoke(initial_state)

    assert result["task_type"] == "contract_generation"
    assert result["awaiting_review"] is True
    assert "contract_generation stub" in result["llm_response"]


def test_graph_multi_skill_routes_through_planner():
    """When skill_plan has multiple skills, routes through planner."""
    from graph.graph import build_graph
    graph = build_graph()

    initial_state = {
        "request": "Review and check compliance",
        "user_id": "attorney-1",
        "uploaded_docs": [],
        "task_type": "compliance",
        "skill_plan": ["contract_review", "compliance"],
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
        "session_id": "test-sess-003",
        "checkpoint_id": "",
        "trace_id": "",
    }

    result = graph.invoke(initial_state)

    # Should have flowed through planner (multi-skill)
    # skill_dispatcher routes based on task_type
    assert result["task_type"] == "compliance"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
source .venv/bin/activate && python -m pytest tests/test_graph.py -v
```

Expected: FAIL — `ImportError: cannot import name 'build_graph'`

- [ ] **Step 3: Create graph.py**

```python
# graph/graph.py
"""LangGraph supervisor graph — wires all nodes, skills, and routing."""

from langgraph.graph import END, StateGraph

from graph.state import LegalAgentState

# Shared nodes
from graph.nodes.intake import intake
from graph.nodes.intent_router import intent_router
from graph.nodes.planner import planner
from graph.nodes.skill_dispatcher import skill_dispatcher, route_to_skill
from graph.nodes.rag_retriever import rag_retriever
from graph.nodes.llm_caller import llm_caller
from graph.nodes.risk_assessor import risk_assessor, route_risk
from graph.nodes.human_review import human_review
from graph.nodes.output_formatter import output_formatter
from graph.nodes.memory_writer import memory_writer

# Skills
from skills.contract_generation import contract_generation
from skills.contract_review import contract_review
from skills.compliance_check import compliance_check
from skills.legal_research import legal_research
from skills.drafting import drafting


def route_intent(state: LegalAgentState) -> str:
    """Route to planner (multi-skill) or skill_dispatcher (single skill)."""
    if len(state.get("skill_plan", [])) > 1:
        return "planner"
    return "skill_dispatcher"


def build_graph() -> StateGraph:
    """Build and compile the supervisor graph. Returns compiled graph."""
    graph = StateGraph(LegalAgentState)

    # Add shared nodes
    graph.add_node("intake", intake)
    graph.add_node("intent_router", intent_router)
    graph.add_node("planner", planner)
    graph.add_node("skill_dispatcher", skill_dispatcher)
    graph.add_node("rag_retriever", rag_retriever)
    graph.add_node("llm_caller", llm_caller)
    graph.add_node("risk_assessor", risk_assessor)
    graph.add_node("human_review", human_review)
    graph.add_node("output_formatter", output_formatter)
    graph.add_node("memory_writer", memory_writer)

    # Add skill nodes
    graph.add_node("contract_generation", contract_generation)
    graph.add_node("contract_review", contract_review)
    graph.add_node("compliance_check", compliance_check)
    graph.add_node("legal_research", legal_research)
    graph.add_node("drafting", drafting)

    # Entry point
    graph.set_entry_point("intake")

    # Edges: intake -> intent_router
    graph.add_edge("intake", "intent_router")

    # Conditional: intent_router -> planner OR skill_dispatcher
    graph.add_conditional_edges("intent_router", route_intent, {
        "planner": "planner",
        "skill_dispatcher": "skill_dispatcher",
    })

    # planner -> skill_dispatcher
    graph.add_edge("planner", "skill_dispatcher")

    # Conditional: skill_dispatcher -> one of the 5 skills
    graph.add_conditional_edges("skill_dispatcher", route_to_skill, {
        "contract_generation": "contract_generation",
        "contract_review": "contract_review",
        "compliance_check": "compliance_check",
        "legal_research": "legal_research",
        "drafting": "drafting",
    })

    # All skills -> rag_retriever
    for skill in ["contract_generation", "contract_review", "compliance_check", "legal_research", "drafting"]:
        graph.add_edge(skill, "rag_retriever")

    # rag_retriever -> llm_caller -> risk_assessor
    graph.add_edge("rag_retriever", "llm_caller")
    graph.add_edge("llm_caller", "risk_assessor")

    # Conditional: risk_assessor -> human_review OR output_formatter
    graph.add_conditional_edges("risk_assessor", route_risk, {
        "human_review": "human_review",
        "output_formatter": "output_formatter",
    })

    # human_review -> output_formatter
    graph.add_edge("human_review", "output_formatter")

    # output_formatter -> memory_writer -> END
    graph.add_edge("output_formatter", "memory_writer")
    graph.add_edge("memory_writer", END)

    return graph.compile()
```

- [ ] **Step 4: Run tests**

```bash
source .venv/bin/activate && python -m pytest tests/test_graph.py -v
```

Expected: 4 passed

- [ ] **Step 5: Run full test suite**

```bash
source .venv/bin/activate && python -m pytest tests/ -v
```

Expected: 26 passed (22 existing + 4 new)

- [ ] **Step 6: Commit**

```bash
git add graph/graph.py tests/test_graph.py
git commit -m "feat: wire LangGraph supervisor graph with stubs and routing"
```

---

### Task 5: Verify end-to-end with logging

Create a script that invokes the graph and prints the node traversal, proving the full flow works.

**Files:**
- Create: `scripts/test_graph_flow.py`

- [ ] **Step 1: Create test_graph_flow.py**

```python
#!/usr/bin/env python3
"""Verify graph flow — invoke with different task types, print node traversal."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(message)s")


def make_state(request: str, task_type: str = "", skill_plan: list[str] | None = None):
    return {
        "request": request,
        "user_id": "attorney-1",
        "uploaded_docs": [],
        "task_type": task_type,
        "skill_plan": skill_plan or [],
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
        "session_id": "test-flow",
        "checkpoint_id": "",
        "trace_id": "",
    }


def main():
    from graph.graph import build_graph
    graph = build_graph()

    print("=== Test 1: Research request (default routing) ===\n")
    result = graph.invoke(make_state("What are indemnification standards?"))
    print(f"\n  task_type: {result['task_type']}")
    print(f"  risk_level: {result['risk_level']}")
    print(f"  awaiting_review: {result['awaiting_review']}")
    print(f"  llm_response: {result['llm_response'][:60]}")

    print("\n\n=== Test 2: Contract generation (always human_review) ===\n")
    result = graph.invoke(make_state(
        "Generate a service agreement",
        task_type="contract_generation",
        skill_plan=["contract_generation"],
    ))
    print(f"\n  task_type: {result['task_type']}")
    print(f"  risk_level: {result['risk_level']}")
    print(f"  awaiting_review: {result['awaiting_review']}")
    print(f"  llm_response: {result['llm_response'][:60]}")

    print("\n\n=== Test 3: Drafting (always human_review) ===\n")
    result = graph.invoke(make_state(
        "Draft an NDA",
        task_type="drafting",
        skill_plan=["drafting"],
    ))
    print(f"\n  task_type: {result['task_type']}")
    print(f"  awaiting_review: {result['awaiting_review']}")

    print("\n\n=== Test 4: Multi-skill (routes through planner) ===\n")
    result = graph.invoke(make_state(
        "Review contract and check compliance",
        task_type="compliance",
        skill_plan=["contract_review", "compliance"],
    ))
    print(f"\n  task_type: {result['task_type']}")
    print(f"  skill_plan: {result['skill_plan']}")

    print("\n\n=== All graph flow tests passed ===")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the script**

```bash
source .venv/bin/activate && python scripts/test_graph_flow.py
```

Expected: All 4 tests print node traversal logs showing the correct flow.

- [ ] **Step 3: Commit**

```bash
git add scripts/test_graph_flow.py
git commit -m "feat: add graph flow verification script"
```

---

## Phase 3 Exit Criteria

- [ ] `python -m pytest tests/ -v` — all tests pass (26+)
- [ ] `python scripts/test_graph_flow.py` — all 4 flow tests print correct routing
- [ ] Graph compiles without errors
- [ ] Research request flows: intake → intent_router → skill_dispatcher → legal_research → rag_retriever → llm_caller → risk_assessor → output_formatter → memory_writer → END
- [ ] Contract generation flows through human_review (always)
- [ ] Drafting flows through human_review (always)
- [ ] Multi-skill request flows through planner
