# Decisions

One note per decision that someone would otherwise ask about again: a choice between options, including a choice *not* to do something. Notes are short, written when the decision is made, and not rewritten afterwards. If a later decision reverses one, write a new note and link the old one from it.

**What goes where:**
- a **decision note** (here): what was chosen and why;
- a **spec** ([`docs/superpowers/specs/`](../superpowers/specs/README.md)): the design of something to build;
- a **follow-up** ([roadmap.md](../roadmap.md)): work still to do;
- a **[shipped-log.md](../shipped-log.md) row**: what was built.

A decision that leads to building something usually gets a note here *and* a spec. The note says why; the spec says how.

**Every note links what it relates to**, on its `Related:` line under the header: the [roadmap.md](../roadmap.md) row it opens, closes or re-rates, and the [spec](../superpowers/specs/README.md) that carries it out. Roadmap rows have no anchors, so link the file and name the row in italics. A note that relates to neither says so: `Related: none`.

## File name

`YYYY-MM-DD-<short-slug>.md`, dated the day the decision was made. For example: [2026-10-28-plans-deleted-once-shipped.md](2026-10-28-plans-deleted-once-shipped.md).

## Format

```markdown
# <The decision, as a short statement>

Date: YYYY-MM-DD · Decided by: <who> · Status: decided | superseded by <link>

Related: [roadmap.md](../roadmap.md) — *<row title>* · [<spec title>](../superpowers/specs/<file>.md)

## Context
What forced a choice: the problem, the constraint, and the evidence (traces, measurements, incidents),
with links.

## Options
1. **<Option>**: what it is, what it costs, what it risks.
2. **<Option>**: …

## Decision
The option chosen, in one or two sentences.

## Why
The reasons this option beat the others. Name the deciding fact. If it was a measurement, give the number
and where it came from.

## Open questions
What is still unknown, and what would make us revisit this. "None" is a valid answer.
```

Keep it short. Most notes fit on one screen. The evidence lives in its links, not in the note.
