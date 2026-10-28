# Implementation plans are deleted once their work ships

Date: 2026-10-28 · Decided by: the project owner · Status: decided

## Context
`docs/superpowers/plans/` held 36 implementation plans: about 42,000 lines, most of the text in `docs/`. Every one was for work that had already shipped.
- **What a plan is:** a step-by-step script that includes the code as it stood that day, with SQLite, the Langfuse SDK and line numbers that have since moved.
- **Why that misleads:** the checkboxes were never ticked, so to a person or an AI assistant searching the repo, each one read as unfinished current work.
- **Why the folder refills:** the planning workflow (`superpowers:writing-plans`) saves every new plan there by default.

## Options
1. **Keep every plan.** The full record stays in the tree, but the stale code snapshots keep misleading and keep showing up in searches.
2. **Move shipped plans to `docs/archive/`.** Out of the way, but still in the tree and in searches, and just as stale.
3. **Delete a plan once its work ships.** Git history keeps it.

## Decision
Option 3. A plan lives in `docs/superpowers/plans/` only while its work is in progress. The change that ships the work deletes the plan. That is the change that lands the work on `main` and adds its row to [shipped-log.md](../shipped-log.md). The spec stays.

## Why
A plan's value ends when its tasks are done:
- the reasoning is in the spec;
- what was built is in the code and the shipped-log row;
- the plan's code blocks are the first thing to go stale.

Nothing linked to the 36 plans (code comments cite only specs), so deleting them cost nothing. `git log --all -- docs/superpowers/plans/<file>` brings any plan back.

## Open questions
- A plan whose work is abandoned halfway: delete it and note that on its [roadmap.md](../roadmap.md) row? That seems right, but it hasn't happened yet.
