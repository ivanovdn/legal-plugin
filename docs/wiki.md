# Legal Plugin — Project Wiki

> Last updated: 2026-10-30 | Python 3.12 | In production on the VM (SRV-AGENT-01) for an attorney pilot since 2026-10-08 — Word add-in on Mac and Windows, model qwen3.6 on Spark, traces in Phoenix | What shipped, and when: [Shipped Log](shipped-log.md)

## What Is This

AI-powered legal assistant for an internal legal team. Attorneys submit requests through the Word add-in (the pilot's main surface), a Chainlit chat UI, or the REST API. A LangGraph supervisor graph routes requests to specialized skills, retrieves grounding context from Qdrant (RAG), and returns structured legal output using a local LLM.

**Air-gapped** — no external API calls. Production runs on a VM (SRV-AGENT-01, 172.20.1.10) with the model (qwen3.6 via Ollama) and Qdrant on a separate internal box (Spark, 172.20.0.22); local dev runs on the developer's Mac.

---

## Constraints (Never Violate)

1. **temperature=0.0** on all LLM calls
2. **SKILL.md is the ceiling** — LLM strictly follows the playbook, no improvising from pretraining
3. **Every research answer must cite a source** — no citation = high risk → escalate (`risk_assessor._check_citations`). A user-attached document counts as the source, and `contract_review` is rated from its own verdict rather than citations
4. **contract_generation and drafting always → human_review** — no exceptions
5. **client_id filter always applied** on retrieval — no cross-client data leakage
6. **Every skill invocation → Postgres audit log** before returning — with one *explicit, visible* exception: when the audit store itself is unreachable, `memory_writer` writes the entry to the application log at ERROR and flags the turn `memory_degraded` (amber banner) rather than killing it. The record is never silently dropped; it moves sinks. Rationale: a turn does not need Postgres to answer, and this codebase's established answer to "the record didn't persist" is to surface it loudly (`review_persist_error`), not to withhold the attorney's work.
7. **No external network calls at runtime** — Ollama, Qdrant and Postgres are all internal. The one exception waiting in the code is the O365 JWKS fetch, which only happens once `SSO_ENABLED` is turned on

---

## Topics

- [architecture.md](architecture.md) — the system diagram, project tree, skills, graph flow and routing, the Chainlit client, the API endpoints and the data models.
- [operations.md](operations.md) — how to run it, the Docker services (with the `app-db` tables), configuration, and the test suites.
- [rag.md](rag.md) — which model runs where (embeddings, Qdrant, reranker; local and VM), the search pipeline, the move to Qdrant's hybrid search, the collections and the ingest pipeline.
- [observability.md](observability.md) — OpenTelemetry tracing, `app.outcome`, the closed degradation vocabulary, what spans carry, Phoenix.
- [roadmap.md](roadmap.md) — every open follow-up, with its priority and the evidence behind it.
- [shipped-log.md](shipped-log.md) — every shipped change, with the reasoning and the measurements that justified it.
- [decisions/](decisions/README.md) — decision notes: context, options, decision, why, open questions.

## Other docs

- **Deep dives:** [context_and_memory_audit.md](context_and_memory_audit.md) (every memory store and how context reaches the model) · [playbook_cross_reference.md](playbook_cross_reference.md) (which source owns which playbook concern) · [output_format_conflict.md](output_format_conflict.md) (why reviews use the current output format).
- **Running the VM:** [deploy-vm.md](deploy-vm.md) (deploy runbook) · [feedback-loop.md](feedback-loop.md) (reading what testers did) · [testing-observability.md](testing-observability.md) (failure drills) · [testing-compaction.md](testing-compaction.md) (manual compaction checks) · [deploy-it-request.md](deploy-it-request.md) (the pending IT requests).
- **For attorneys:** [tester-setup.md](tester-setup.md) (installing the add-in) · [using-the-assistant.md](using-the-assistant.md) (what it does and what is recorded).
- **History:** [superpowers/specs/](superpowers/specs/README.md) (the design behind each feature) · [archive/](archive/README.md) (the plans from before July 2026, and superseded notes). The step-by-step implementation plans (`superpowers/plans/`) were removed on 2026-10-28, once everything in them had shipped; the paths [shipped-log.md](shipped-log.md) cites are in git history (`git log --all -- <path>`).
