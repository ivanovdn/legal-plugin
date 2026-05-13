---
name: contract-generation-demo
description: Generate a new service agreement draft from 5 historical LegalCo service agreements. Demo dataset only — client_id="demo", collection="case_history".
metadata:
  version: demo-1.0
  dataset: 5 synthetic LegalCo service agreements
  clause_types: 7
---

# Contract Generation — Demo Playbook

## Context

You are generating a new service agreement for LegalCo Inc. You have access to 5 historical signed service agreements in the case_history collection. All 5 were signed by LegalCo as the service provider.

**Dataset facts you must know before searching:**
- `collection`: `case_history`
- `client_id`: `demo`
- `doc_type`: `contract`
- `contract_type`: `services`
- All 5 contracts are governed by Delaware law
- All 5 are short-form service agreements (services scope, payment, IP, confidentiality, liability, termination, governing law)

---

## Clause types in this dataset

Search only for these 7 clause types. Do not search for anything else — other clause types do not exist in this dataset.

| clause_type | What it covers |
|---|---|
| `services_scope` | Description of services, SOW references |
| `payment_terms` | Invoice payment period, late fees |
| `ip_ownership` | Who owns deliverables and tools |
| `confidentiality` | Non-disclosure obligations, survival period |
| `cap_on_liability` | Liability cap amount and exclusions |
| `termination_convenience` | Notice period, termination rights |
| `governing_law` | Governing law and jurisdiction |

---

## Known patterns — use these to reason

You will find these patterns in the dataset. Use them to inform generation and the deviation report.

| Clause | Pattern | Notes |
|---|---|---|
| Governing law | Delaware — all 5 contracts | Consistent company standard — use without flagging |
| Payment terms | 30 days — 3 contracts · 45 days — 2 contracts | Majority: 30 days |
| Termination notice | 30 days — 3 contracts · 60 days — 2 contracts | Majority: 30 days |
| IP ownership | Client owns all IP — 3 contracts · Vendor retains tools — 2 contracts | Split — flag for attorney |
| Liability cap | 12 months — 2 · 6 months — 2 · 3 months — 1 | Variable — flag range, note 12 months most favourable to LegalCo |
| Confidentiality survival | 3 years — all 5 contracts | Consistent — use without flagging |

---

## Workflow — 4 steps

### Step 1 — Search for each clause type
Call `search_legal` once per clause type with:
```
collection = "case_history"
client_id  = "demo"
doc_type   = "contract"
clause_type = <one of the 7 above>
```
Do not call `search_legal` more than 7 times total. One call per clause type.

### Step 2 — Extract patterns
Call `extract_clauses` on the top 2–3 results per clause type. Note the variation — do not average or invent. Record:
- What the majority position is
- What the range is (if variable)
- Which `doc_id` supports each pattern

### Step 3 — Generate the contract
Produce a short service agreement in this order:
1. Title, parties, effective date
2. Services scope
3. Payment terms
4. IP ownership
5. Confidentiality
6. Limitation of liability
7. Termination
8. Governing law
9. Signature block

Rules:
- Use formal legal English
- Tag each clause: `[Source: doc_id]`
- Fill deal variables from the request (party names, dates, amounts)
- Mark any variable not provided: `[TO BE CONFIRMED]`
- Include `DRAFT — FOR ATTORNEY REVIEW ONLY` at the top

### Step 4 — Deviation report
After the contract, output this block:

```
DEVIATION REPORT
================
Governing law:    Delaware [consistent — 5/5 contracts]
Payment terms:    [X days] [majority / variant — note]
Termination:      [X days] [majority / variant — note]
IP ownership:     [position used] ⚠ SPLIT PATTERN — attorney to confirm
Liability cap:    [X months] ⚠ VARIABLE (3/6/12 months seen) — attorney to confirm
Confidentiality:  3 years [consistent — 5/5 contracts]
Sources cited:    [list of doc_ids used]
```

---

## Escalation

Call `escalate` if:
- Fewer than 2 source contracts retrieved for any clause type
- Request is not for a service agreement — this playbook covers services only
- `client_id` is missing from the request

---

## Output format

```
DRAFT — FOR ATTORNEY REVIEW ONLY
=================================
SERVICE AGREEMENT

[Title]
Effective Date: [date]

Between: LegalCo Inc., a Delaware corporation ("Service Provider")
And:     [Client name] ("Client")

1. SERVICES [Source: doc_id]
...

2. PAYMENT [Source: doc_id]
...

3. INTELLECTUAL PROPERTY [Source: doc_id] ⚠ split pattern
...

4. CONFIDENTIALITY [Source: doc_id]
...

5. LIMITATION OF LIABILITY [Source: doc_id] ⚠ variable
...

6. TERMINATION [Source: doc_id]
...

7. GOVERNING LAW [Source: doc_id]
...

SIGNATURE BLOCK
...

---
DEVIATION REPORT
...
```
