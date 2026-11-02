# History notes are their own kind, kept out of default search

Date: 2026-10-30 · Decided by: the project owner · Status: decided

Related: [roadmap.md](../roadmap.md) — *Index the legal reference books for retrieval-augmented checking* · [Reference books design](../superpowers/specs/2026-10-30-reference-books-design.md) · [chunk model](2026-10-30-reference-book-chunk-model.md) · [citations](2026-10-30-reference-book-citations.md)

## Context
These facts come from the Circular 92 exploration (2026-10-30), on chapters 1 and 5:
- **The notes are history, not law.** Each chapter ends with numbered amendment-history notes, not statutory text. They make up 77 of the 568 chunks, and the longest is 5,210 tokens.
- **They outrank the rules.** In a smoke test, a note outranked the right answer in up to 8 of 54 definition lookups.
- **Some carry effective dates.** 17 of the 77 say when a provision takes or took effect. For example, ch.1 n.13 says paragraph (5) of the definition of "international agreement" took effect on March 6, 2002.
- **Each note has a home in the rule text.** Its marker sits at a known point, and all 77 notes are linked to their markers.
- **The appendices hold other law.** Besides Title 17 there are other U.S.C. titles, uncodified Acts and TRIPs, which is a treaty.

## Options
1. **Leave the notes out.** Questions about amendments and effective dates lose their answer.
2. **Index them like rule text.** Measured worse: a note won up to 8 of 54 lookups. And a note quoted as if it were law misleads.
3. **Attach each note to its rule's chunk.** Notes run to 5,210 tokens, past the chunk budget and the 2,048-token embedding window, and they would dilute the rule's vector.
4. **A separate collection for notes.** A second collection to keep in step, for something a payload field already does.
5. **Same collection, ranked lower,** with a formula query. Not tried. It is a weight to tune, and a note still wins when the rule scores low.
6. **A `kind` on every chunk.** Default search covers rules and definitions; notes come by link or on request.

## Decision
Option 6:
- **Four kinds:** `rule`, `definition`, `history note` and `repealed`. The repealed kind covers the §509 and §601 stubs, which are reached by direct lookup only.
- **Rules link to their notes.** Each rule chunk carries the `ref_id`s of its notes.
- **Effective dates are flagged, not attached.** When a returned rule has an effective-date note, the result says so ("effective-date note: <ref_id>"), without the note's text, and the model or the attorney can fetch it.
- **Notes are searched only on request.** Their citations say "amendment history, not statutory text". Long notes are split the same way as rules.
- **Treaties stay in default search.** They are labelled by `source_type` = treaty, so an answer can't present TRIPs as U.S. law.

## Why
- **The deciding measurement:** a note won up to 8 of 54 definition lookups. Options 1 and 3 lose or blur the effective dates.
- **A field filter beats a weight.** It removes notes from default results completely and needs no tuning.
- **The links already exist,** so "the history of this rule" needs no search.

## Open questions
- **Recognising a history question:** either a tool parameter or a router. Measure both on the test set.
- **When to revisit option 5:** if history questions fail under the filter.
