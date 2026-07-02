# Step 0 — Reliability floor (Redis must fail loud)

## Problem

If Redis is down, the checkpointer returns `None` and every turn silently becomes stateless. No error, no signal. The user keeps talking; the agent quietly has no memory. The same fail-loud principle must extend to the SQLite review store introduced in Step 1 — a failed review write must never be silent.

## Why this is first

Steps 1 and 2 write **more** state to Redis (persisted reviews, session history). A silent-stateless failure mode becomes more damaging exactly as memory becomes more central. If the persisted review layer is built on a store that can silently return nothing, the new memory will vanish intermittently with no way to tell that it happened — which presents as "the feature doesn't work sometimes," the worst kind of bug to chase.

## What to build

The bar is **fail loud, not fail silent.** Not necessarily fail *hard*. The point is that a degraded state is observable.

Minimum:
- When the checkpointer / store cannot reach Redis, surface it. Two acceptable shapes:
  - **Visible degraded mode:** the turn proceeds but the UI (or the response) indicates memory is unavailable this turn, and it's logged as an error, not a debug line.
  - **Explicit failure:** the turn fails with a clear message rather than proceeding as if stateless were normal.
- Decide one and make it consistent across both the review path and the chat path.

## Decision to confirm

- **Degrade-with-warning vs. hard-fail** when a store is unavailable. Recommendation: **degrade with a visible warning** for read paths (don't block the lawyer mid-task), **but** never let a *write* of a persisted review (Step 1) silently no-op — a lost review write should be loud, because the user will believe their review was saved.

Note the two stores have different stakes here (see `02`, "Two stores, two roles"): the **Redis checkpointer** going down costs the live thread state — degrade-with-warning is acceptable. The **SQLite review store** failing to write costs the durable legal record — that must be loud. Apply the stricter rule to the review-store writes.

## Done when

- Pulling Redis out from under a running session produces a visible/logged signal, not silent statelessness.
- A failed persisted-review write (once Step 1 exists) is never silent.

## Explicitly out of scope

- Redis HA, failover, clustering. This is about *observability of the failure*, not eliminating it.
