"""
scripts/generate_demo_contracts.py

Generates 5 synthetic service agreements into data/demo_contracts/
then ingests them into the case_history Qdrant collection.

Intentional variation across contracts:
  - Liability cap:        6 months (×2) vs 12 months (×2) vs 3 months (×1)
  - Termination notice:  30 days (×3)  vs 60 days (×2)
  - Payment terms:       30 days (×3)  vs 45 days (×2)
  - IP ownership:        client (×3)   vs vendor (×2)
  - Governing law:       Delaware throughout (consistent "company standard")

This gives the agent clear patterns to extract and report on.
Run: python scripts/generate_demo_contracts.py
"""

import json
import sys
import uuid
from pathlib import Path

# Allow running as script from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# Contract texts — short, realistic, clause-level variation
# ---------------------------------------------------------------------------

CONTRACTS = [
    {
        "doc_id": "svc_acme_2022",
        "doc_title": "Services Agreement — Acme Corp (2022)",
        "contract_type": "services",
        "jurisdiction": "US-DE",
        "text": """SERVICES AGREEMENT

This Services Agreement ("Agreement") is entered into as of March 1, 2022, between LegalCo Inc., a Delaware corporation ("Company"), and Acme Corp, a Delaware corporation ("Client").

1. SERVICES
Company shall provide software development and consulting services as described in each Statement of Work ("SOW") agreed by the parties.

2. PAYMENT
Client shall pay Company within 30 days of invoice. Late payments accrue interest at 1.5% per month.

3. INTELLECTUAL PROPERTY
All work product created by Company under this Agreement shall be the exclusive property of Client upon full payment. Company retains no rights to deliverables.

4. CONFIDENTIALITY
Each party shall keep the other's confidential information strictly confidential and shall not disclose it to third parties without prior written consent. This obligation survives termination for 3 years.

5. LIMITATION OF LIABILITY
Company's total liability under this Agreement shall not exceed the fees paid by Client in the 6 months preceding the claim. Neither party shall be liable for indirect, incidental, or consequential damages.

6. TERMINATION
Either party may terminate this Agreement for convenience upon 30 days written notice. Client shall pay for all services rendered through the termination date.

7. GOVERNING LAW
This Agreement is governed by the laws of the State of Delaware, without regard to conflict of law principles.

8. ENTIRE AGREEMENT
This Agreement constitutes the entire agreement between the parties and supersedes all prior negotiations, representations, or agreements.

IN WITNESS WHEREOF, the parties have executed this Agreement as of the date first written above.

LEGALCO INC.                          ACME CORP
By: ___________________               By: ___________________
Name: Sarah Chen                      Name: John Miller
Title: CEO                            Title: VP Procurement
""",
    },
    {
        "doc_id": "svc_brightwave_2022",
        "doc_title": "Services Agreement — BrightWave Ltd (2022)",
        "contract_type": "services",
        "jurisdiction": "US-DE",
        "text": """SERVICES AGREEMENT

This Services Agreement ("Agreement") is dated September 15, 2022, between LegalCo Inc., a Delaware corporation ("Company"), and BrightWave Ltd, a UK company ("Client").

1. SERVICES
Company shall provide data analytics and reporting services as set out in Statements of Work executed by both parties from time to time.

2. PAYMENT
Client shall pay all invoices within 45 days of the invoice date. Disputed invoices must be raised in writing within 10 days of receipt.

3. INTELLECTUAL PROPERTY
Company retains ownership of all tools, frameworks, and methodologies used to deliver the services. Client receives a non-exclusive licence to use deliverables for its internal business purposes only.

4. CONFIDENTIALITY
Each party agrees not to disclose the other's confidential information to any third party. The obligation of confidentiality shall survive termination of this Agreement for a period of 3 years.

5. LIMITATION OF LIABILITY
Company's aggregate liability for all claims under this Agreement shall not exceed the total fees paid in the 12 months prior to the event giving rise to the claim. Liability for death, personal injury, or fraud is not limited.

6. TERMINATION
Either party may terminate this Agreement on 60 days written notice. On termination, all outstanding invoices become immediately due.

7. GOVERNING LAW
This Agreement shall be governed by and construed in accordance with the laws of Delaware, USA.

8. MISCELLANEOUS
No waiver of any breach shall constitute a waiver of any subsequent breach. If any provision is found unenforceable, the remainder continues in full force.

LEGALCO INC.                          BRIGHTWAVE LTD
By: ___________________               By: ___________________
Name: Sarah Chen                      Name: Emma Wright
Title: CEO                            Title: COO
""",
    },
    {
        "doc_id": "svc_nordex_2023",
        "doc_title": "Services Agreement — Nordex Solutions (2023)",
        "contract_type": "services",
        "jurisdiction": "US-DE",
        "text": """PROFESSIONAL SERVICES AGREEMENT

Dated: February 10, 2023
Parties: LegalCo Inc. ("Provider") and Nordex Solutions GmbH ("Client")

1. SCOPE OF SERVICES
Provider will deliver UX design and product strategy services per mutually agreed Statements of Work. Each SOW shall specify deliverables, timeline, and fees.

2. FEES AND PAYMENT
Client shall pay Provider's invoices within 30 days of the invoice date. Provider may suspend services if any invoice remains unpaid for more than 15 days after the due date.

3. OWNERSHIP OF DELIVERABLES
Upon receipt of full payment, all deliverables produced specifically for Client become the property of Client. Provider may retain and reuse general know-how and pre-existing materials.

4. CONFIDENTIAL INFORMATION
Both parties shall protect the other's confidential information using at least the same degree of care they use for their own confidential information. This clause survives termination for 3 years.

5. LIABILITY
Provider's maximum liability to Client for any and all claims shall be limited to the fees paid by Client in the 12 months preceding the relevant claim. In no event will either party be liable for lost profits or consequential damages.

6. TERM AND TERMINATION
This Agreement commences on the date signed and continues until terminated. Either party may terminate for convenience on 30 days written notice, or immediately for material breach uncured within 14 days of written notice.

7. GOVERNING LAW
Delaware law governs this Agreement. The parties submit to the exclusive jurisdiction of the courts of Delaware.

LEGALCO INC.                          NORDEX SOLUTIONS GMBH
By: ___________________               By: ___________________
Name: Sarah Chen                      Name: Klaus Bauer
Title: CEO                            Title: Managing Director
""",
    },
    {
        "doc_id": "svc_palmridge_2023",
        "doc_title": "Services Agreement — Palmridge Inc (2023)",
        "contract_type": "services",
        "jurisdiction": "US-DE",
        "text": """SERVICES AGREEMENT

This Agreement is made as of July 1, 2023 between LegalCo Inc., Delaware ("Service Provider") and Palmridge Inc., California ("Client").

1. SERVICES
Service Provider agrees to provide marketing technology consulting services as described in each SOW. Service Provider shall perform services in a professional and workmanlike manner.

2. INVOICING AND PAYMENT
Service Provider shall invoice monthly. Client shall pay each invoice within 45 days. Invoices unpaid after 60 days are subject to a 2% monthly late fee.

3. INTELLECTUAL PROPERTY
All intellectual property created by Service Provider in the course of performing services is hereby assigned to Client, effective upon full payment of all related fees. Service Provider warrants it has authority to make this assignment.

4. CONFIDENTIALITY
The parties shall not disclose each other's confidential information and shall use it only for the purposes of this Agreement. The confidentiality obligation survives expiry or termination for 3 years.

5. LIMITATION OF LIABILITY
Service Provider's aggregate liability shall not exceed the lesser of (a) fees paid in the 6 months prior to the claim, or (b) USD 50,000. Neither party shall be liable for indirect or consequential damages.

6. TERMINATION
Client may terminate for convenience on 60 days written notice. Service Provider may terminate if Client fails to pay any invoice within 30 days of the due date.

7. GOVERNING LAW
This Agreement is governed by the laws of the State of Delaware.

LEGALCO INC.                          PALMRIDGE INC.
By: ___________________               By: ___________________
Name: Sarah Chen                      Name: Diana Park
Title: CEO                            Title: CFO
""",
    },
    {
        "doc_id": "svc_irongate_2024",
        "doc_title": "Services Agreement — Irongate Partners (2024)",
        "contract_type": "services",
        "jurisdiction": "US-DE",
        "text": """PROFESSIONAL SERVICES AGREEMENT

Effective Date: January 15, 2024
Between: LegalCo Inc., a Delaware corporation ("LegalCo") and Irongate Partners LLC, a New York LLC ("Client")

1. SERVICES
LegalCo shall provide legal technology and workflow automation services as specified in Statements of Work. LegalCo may use qualified subcontractors with Client's prior written consent.

2. PAYMENT TERMS
Client shall pay invoices within 30 days. LegalCo reserves the right to charge interest at the statutory rate on overdue amounts. Expenses must be pre-approved and are invoiced at cost.

3. INTELLECTUAL PROPERTY
LegalCo retains all rights in its proprietary platform, tools, and methodologies. Client receives a limited, non-exclusive, non-transferable licence to use deliverables internally. Custom code written solely for Client becomes Client property on final payment.

4. CONFIDENTIALITY
Each party shall hold the other's confidential information in strict confidence and shall not disclose it to third parties. This obligation continues for 3 years after termination.

5. LIABILITY CAP
LegalCo's total liability for all claims arising under or in connection with this Agreement shall not exceed the fees paid by Client in the 3 months immediately preceding the claim. This cap applies to all causes of action in aggregate.

6. TERMINATION
Either party may terminate for convenience on 30 days written notice. LegalCo may terminate immediately if Client is insolvent or has not paid a due invoice within 45 days of notice.

7. GOVERNING LAW AND DISPUTES
This Agreement is governed by Delaware law. Any dispute shall first be submitted to non-binding mediation before litigation.

8. ENTIRE AGREEMENT
This Agreement supersedes all prior discussions and agreements. Amendments must be in writing and signed by both parties.

LEGALCO INC.                          IRONGATE PARTNERS LLC
By: ___________________               By: ___________________
Name: Sarah Chen                      Name: Marcus Webb
Title: CEO                            Title: Managing Partner
""",
    },
]

# ---------------------------------------------------------------------------
# Clause extraction — tag each text segment with its clause_type
# Maps section headings to CUAD-compatible clause_type values
# ---------------------------------------------------------------------------

CLAUSE_MARKERS = [
    ("SERVICES",            "services_scope"),
    ("SCOPE OF SERVICES",   "services_scope"),
    ("FEES AND PAYMENT",    "payment_terms"),
    ("INVOICING AND PAYMENT","payment_terms"),
    ("PAYMENT TERMS",       "payment_terms"),
    ("PAYMENT",             "payment_terms"),
    ("INTELLECTUAL PROPERTY","ip_ownership"),
    ("OWNERSHIP OF DELIVERABLES","ip_ownership"),
    ("CONFIDENTIALITY",     "confidentiality"),
    ("CONFIDENTIAL INFORMATION","confidentiality"),
    ("LIMITATION OF LIABILITY","cap_on_liability"),
    ("LIABILITY CAP",       "cap_on_liability"),
    ("LIABILITY",           "cap_on_liability"),
    ("TERM AND TERMINATION","termination_convenience"),
    ("TERMINATION",         "termination_convenience"),
    ("GOVERNING LAW",       "governing_law"),
    ("GOVERNING LAW AND DISPUTES","governing_law"),
    ("ENTIRE AGREEMENT",    "entire_agreement"),
    ("MISCELLANEOUS",       "boilerplate"),
]


def extract_clause_chunks(contract: dict) -> list[dict]:
    """Split contract text into clause-level chunks."""
    import re
    text = contract["text"]
    chunks = []

    # Split on numbered section headings like "1. SERVICES" or "1. SCOPE"
    sections = re.split(r"\n(\d+\.\s+[A-Z][A-Z\s/&]+)\n", text)

    # sections alternates: [preamble, heading1, body1, heading2, body2, ...]
    # First element is preamble (parties / date block)
    if sections:
        preamble = sections[0].strip()
        if len(preamble) > 30:
            chunks.append(_make_chunk(contract, "preamble", preamble))

    i = 1
    while i < len(sections) - 1:
        heading = sections[i].strip()
        body = sections[i + 1].strip() if i + 1 < len(sections) else ""
        clause_type = _heading_to_clause_type(heading)
        clause_text = f"{heading}\n{body}".strip()
        if len(clause_text) > 20:
            chunks.append(_make_chunk(contract, clause_type, clause_text))
        i += 2

    return chunks


def _heading_to_clause_type(heading: str) -> str:
    heading_upper = heading.upper()
    for marker, clause_type in CLAUSE_MARKERS:
        if marker in heading_upper:
            return clause_type
    return "general"


def _make_chunk(contract: dict, clause_type: str, text: str) -> dict:
    return {
        "chunk_id":       str(uuid.uuid4()),
        "doc_id":         contract["doc_id"],
        "doc_title":      contract["doc_title"],
        "doc_filename":   f"{contract['doc_id']}.txt",
        "doc_type":       "contract",
        "contract_type":  contract["contract_type"],
        "clause_type":    clause_type,
        "client_id":      "demo",
        "jurisdiction":   contract["jurisdiction"],
        "sensitivity":    "internal",
        "section":        clause_type,
        "section_number": "",
        "clause":         clause_type,
        "clause_number":  "",
        "section_display": clause_type.replace("_", " ").title(),
        "text":           text,
        "char_count":     len(text),
        "chunk_index":    0,
        "last_updated":   "",
    }


# ---------------------------------------------------------------------------
# Save raw contract files + ingest
# ---------------------------------------------------------------------------

def save_contracts(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for c in CONTRACTS:
        path = output_dir / f"{c['doc_id']}.txt"
        path.write_text(c["text"], encoding="utf-8")
        print(f"  Saved {path}")


def build_all_chunks() -> list[dict]:
    all_chunks = []
    for contract in CONTRACTS:
        chunks = extract_clause_chunks(contract)
        print(f"  {contract['doc_id']}: {len(chunks)} clause chunks")
        all_chunks.extend(chunks)
    return all_chunks


def ingest_chunks(chunks: list[dict], batch_size: int = 32) -> None:
    from config import get_settings
    from rag.embeddings import embed_texts
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct

    settings = get_settings()
    client = QdrantClient(url=settings.qdrant_url)
    collection = "case_history"

    print(f"\nUpserting {len(chunks)} chunks into '{collection}'...")
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i: i + batch_size]
        texts = [chunk["text"] for chunk in batch]
        vectors = embed_texts(texts)
        points = []
        for chunk, vector in zip(batch, vectors):
            points.append(PointStruct(
                id=chunk["chunk_id"],
                vector=vector,
                payload={k: v for k, v in chunk.items() if k != "chunk_id"},
            ))
        client.upsert(collection_name=collection, points=points)
        print(f"  {min(i + batch_size, len(chunks))}/{len(chunks)}")
    print("Done.")


# ---------------------------------------------------------------------------
# Clause variation summary — shows the agent what patterns exist
# ---------------------------------------------------------------------------

VARIATION_SUMMARY = """
DEMO DATASET — CLAUSE VARIATION SUMMARY
========================================
5 service agreements, all from LegalCo Inc. perspective.

Liability cap:
  6 months  → svc_acme_2022, svc_palmridge_2023
  12 months → svc_brightwave_2022, svc_nordex_2023
  3 months  → svc_irongate_2024

Termination notice:
  30 days → svc_acme_2022, svc_nordex_2023, svc_irongate_2024
  60 days → svc_brightwave_2022, svc_palmridge_2023

Payment terms:
  30 days → svc_acme_2022, svc_nordex_2023, svc_irongate_2024
  45 days → svc_brightwave_2022, svc_palmridge_2023

IP ownership:
  Client owns all IP    → svc_acme_2022, svc_nordex_2023, svc_palmridge_2023
  Vendor retains tools  → svc_brightwave_2022, svc_irongate_2024

Governing law:
  Delaware → all 5 (consistent company standard)

Expected agent behaviour on generation request:
- Governing law: Delaware — consistent, use with high confidence
- Payment: 30 days majority pattern, note 45-day variants
- Termination: 30 days majority pattern
- IP: split — flag for attorney to confirm position
- Liability: variable (3/6/12 months) — flag range, recommend 12 months
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import sys
    dry_run = "--dry-run" in sys.argv

    print("=== Demo contract dataset ===\n")
    print("Saving raw contract files...")
    output_dir = Path("data/demo_contracts")
    save_contracts(output_dir)

    print("\nExtracting clause chunks...")
    chunks = build_all_chunks()
    print(f"\nTotal: {len(chunks)} clause chunks from {len(CONTRACTS)} contracts")

    print(VARIATION_SUMMARY)

    if dry_run:
        sample = Path("data/demo_chunks_sample.json")
        sample.write_text(json.dumps(chunks, indent=2), encoding="utf-8")
        print(f"--dry-run: chunks written to {sample}, skipping ingest.")
        return

    ingest_chunks(chunks)
    print("\nDemo dataset ready. Try a generation request:")
    print('  python scripts/test_query.py "Generate a services agreement for a new client, Vertex Systems"')


if __name__ == "__main__":
    main()
