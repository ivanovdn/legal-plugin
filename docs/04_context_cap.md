# Step 3 — Context cap guard on the chat path

Solves: the whole document is re-sent every turn on the chat path, **uncapped** — unlike the review path, which caps the MSA at 24k chars. As Step 2 adds the MSA and playbook to chat context, document + findings + playbook + MSA now compete for one fixed local-model context window. Without a guard, a large enough document overflows it — a hard failure, not a graceful degradation.

## Why now (not earlier, not later)

- **Not earlier:** while the chat path carried only the document, there was no competition for the window — the doc had it all to itself, so the uncapped re-send was tolerable.
- **Not later:** Step 2 is precisely what activates the competition. The cap must land with/after Step 2 so the new additions can't blow the window.

This is the context-budget tension surfacing in practice: every token spent re-sending document text is a token unavailable for the memory and grounding that make the agent good. Today that trade is invisible; Step 2 makes it real.

## What to build (minimum)

A **size guard on the chat path**, mirroring the existing 24k-char MSA cap on the review path:
- If the document (or the assembled context) exceeds a threshold, cap/truncate with a clear rule, and ideally signal it rather than silently dropping content.
- Keep it crude. The goal is removing the sharp edge (silent overflow → failure), not clever compression.

## What NOT to build yet

- **No clause segmentation, no retrieval-narrowing, no per-clause addressing.** That is the eventual endgame (full doc for broad early questions; narrow to the clause under discussion for later questions, spending the reclaimed budget on memory) — but it's a larger architectural change and not needed to remove the overflow risk.
- The signal to build narrowing is measured, not guessed: when the token telemetry (`ollama_usage`, already wired) shows the document's share of the window leaving no comfortable room for findings + playbook + MSA. Build narrowing then, with data, not now.

## Done when

- A large document on the chat path no longer risks silently overflowing the context window once MSA + playbook are also present.
- The cap behavior is observable (logged/surfaced), consistent with the review path's MSA cap.
