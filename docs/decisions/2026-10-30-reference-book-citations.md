# Book answers cite a `ref_id`; code checks it and renders the citation

Date: 2026-10-30 · Decided by: the project owner · Status: decided

Related: [roadmap.md](../roadmap.md) — *Index the legal reference books for retrieval-augmented checking* · [Reference books design](../superpowers/specs/2026-10-30-reference-books-design.md) · [chunk model](2026-10-30-reference-book-chunk-model.md) · [history notes](2026-10-30-reference-book-history-notes.md)

## Context
- **What a citation must say:** book, edition, section and page, for example `17 U.S.C. §122(f)(1) — Circular 92 (Dec. 2025), p. 143`. The Circular 92 exploration (2026-10-30) found five formats:
  - a statute;
  - a definition;
  - another title, in an appendix;
  - an uncodified Act;
  - a history note.

  A citation may name a path below its chunk: the §122(f) chunk spans pp. 143–144, and (f)(1) is on p. 143.
- **How the check works today:** the agents record the `doc_id`s their searches returned, and [`_check_citations`](../../graph/nodes/risk_assessor.py) passes a turn whose answer names one of them. The check reads whole ids, and only from search results, since 2026-10-30 ([shipped-log.md](../shipped-log.md), *Citation check reads whole doc_ids, and only from search results*).
- **Why the model shouldn't write pages:** pages and editions are what a model copies wrongly, and a wrong page looks right.

## Options
1. **The model writes the full citation.** It can change a page or an edition, and checking means parsing five formats back.
2. **Keep `doc_id:`, with a UUID for each book chunk.** The answer shows UUIDs, and elsewhere `doc_id` means a whole document (`get_document`, `delete_document`).
3. **The model writes `ref_id: <id>`; code checks the id and renders the citation from stored fields.**
4. **Numbered sources pointing into the turn's results.** The numbers mean nothing once the turn is over, in history or in compaction quotes.
5. **No inline citations; code appends the retrieved sources.** That shows what was retrieved, not what supports each claim.
6. **A `ref_id` plus a short verbatim quote, checked against the chunk's text.** This catches a misquote ("shall not" → "may not"). It costs a longer answer, and a model that quotes loosely fails the check.

## Decision
Option 3:
- **Rule 4** in CLAUDE.md: client documents cite `doc_id`; reference books cite `ref_id`.
- **Earlier retrievals count.** A cited `ref_id` must have been retrieved somewhere in the conversation, not necessarily this turn.
- **A sub-path is accepted** when it lies under a retrieved chunk and exists in the parsed tree.
- **Anything else raises the turn's risk,** whether the id wasn't retrieved in the conversation or doesn't exist, just as a missing citation does.
- **The edition is part of the `ref_id`** ([chunk model](2026-10-30-reference-book-chunk-model.md)).
- **Rendering runs in the backend,** before the report. The turn's stored text keeps the raw `ref_id`s.
- **Option 6 comes later,** not in this build.

## Why
- **The deciding fact:** the model never writes a page or an edition, so neither can be wrong. Each citation is checked by exact lookup.
- **`ref_id`s are stable across turns,** so a citation from an earlier turn, or inside a compaction quote, still points at the right passage.

## Open questions
- **The check proves existence, not support.** A retrieved, existing `ref_id` can still be cited for a claim its text doesn't make. The candidates are option 6 and a verifier (MiniCheck, in the research notes). Revisit both with the test set.
