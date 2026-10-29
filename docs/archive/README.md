# Archive

> Part of the [project wiki](../wiki.md).

Notes kept for their reasoning after the work they describe shipped or was superseded. Nothing here describes the current code; for that, see the [wiki](../wiki.md) and [the specs](../superpowers/specs/README.md). Newest first.

- 2026-08-11 · [Tracing scope: one instrumentation, swappable backend](tracing-otel-migration-scope.md) — the scoping note for replacing the Langfuse SDK with OpenTelemetry, so the trace backend becomes a config choice (Langfuse locally, Phoenix on the VM). *Superseded by the [OpenTelemetry tracing migration spec](../superpowers/specs/2026-08-11-otel-tracing-migration-design.md); shipped as `feat/otel-tracing`.*
- 2026-07-07 · [Legal Agent — Upgrade Research](legal-agent-upgrade-research.md) — a "what's next" brainstorm covering model evaluation, LLM-as-judge, competitor UX ideas and a self-improving agent loop. *Research capture, no build committed; competitor ideas #1 and #8 became the [Word add-in quick UX wins spec](../superpowers/specs/2026-07-07-word-addin-quick-ux-design.md).*
- 2026-06-29 · [Legal agent — memory & context work: implementation plan](00_PLAN.md) — the handoff for the chat memory and grounding work: why the Findings and Chat tabs know different things, and the four steps that follow. *Shipped, as the [Chat memory & grounding spec](../superpowers/specs/2026-06-29-chat-memory-grounding-design.md).*
- 2026-06-29 · [Step 0 — Reliability floor (Redis must fail loud)](01_reliability_floor.md) — a Redis outage must be visible rather than silently turning every turn stateless. *Shipped.*
- 2026-06-29 · [Step 1 — Persist & inject the review](02_persist_findings.md) — store the full review against the document and inject it into chat, so follow-ups recall findings instead of re-deriving them. *Shipped.*
- 2026-06-29 · [Step 2 — MSA + playbook on the chat path](03_msa_playbook_on_chat.md) — give the Chat tab the playbook and the governing MSA that the Findings tab already uses. *Shipped.*
- 2026-06-29 · [Step 3 — Context cap guard on the chat path](04_context_cap.md) — cap what the chat path re-sends each turn, now that the document, findings, playbook and MSA compete for one context window. *Shipped.*
