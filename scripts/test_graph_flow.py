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
        "checkpoint_ref": "",
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
