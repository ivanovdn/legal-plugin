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
