# Step 2 — MSA + playbook on the chat path

Solves: the Chat tab reasons with no firm-position grounding. "Does this SOW conflict with the MSA?" asked in chat runs with **no MSA in context**, and chat redlining ("soften the indemnity") happens with **no playbook** — so the agent edits toward generic contract norms, not firm policy.

## Why this is the highest-value single gap

The Chat tab is the surface where edits actually get made ("changes by chat command" is the primary interaction). It is currently the *least* grounded surface in the system — the opposite of what it should be. The Findings tab knows the playbook and attaches the governing MSA for SOWs; the chat path was built as a deliberately lean, tool-free fast path and skipped both. The result is a conversational editor that doesn't know the firm's rules or the governing agreement it's redlining against.

## What to build

Mirror the Findings-tab grounding onto the chat path:

1. **Playbook on chat.** Detect contract type (logic already exists on the review path), load the matching playbook bundle, prepend it to the chat path's message assembly the same way the review path does. Now "soften the indemnity" can soften *toward the firm's fallback position* — or push back ("that breaches our cap; the fallback we accept is X").
2. **MSA on chat (for SOWs).** Attach the governing MSA on the chat path too, so SOW-vs-MSA conflict questions work conversationally. Reuse the existing 24k-char MSA cap from the review path (see Step 3 — this is exactly why the cap matters).

## Ordering note

Do playbook first if splitting the work — it's always relevant and smaller. MSA attach is for SOW-vs-MSA questions specifically and is larger (hence the cap). Both are the same pattern: take what the review path already loads and make it available on the chat path.

## Interaction with Step 1 and Step 3

- With Step 1 (persisted findings) **and** this step both done, the chat path finally has the two things it needs to be a real redlining surface: **what we already concluded about this contract** (findings) and **what the firm's rules + governing agreement are** (playbook + MSA).
- This step is what makes Step 3 (context cap) non-optional: document + findings + playbook + MSA now compete for one context window on a local model. Land the cap alongside this.

## Decision to confirm

- **Mirror the MSA immediately, or playbook-only first?** Recommendation: **include the MSA** — it's the concrete payload behind the headline complaint ("does this SOW conflict with the MSA?" failing in chat). Playbook-only would leave the most-cited gap open.

## Done when

- Asking "does this SOW conflict with the MSA?" in chat returns an answer grounded in the actual MSA.
- Chat redlining reflects firm playbook positions (cites/honors fallbacks), not generic contract norms.
