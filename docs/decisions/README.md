# Decisions

One note per decision that someone would otherwise ask about again: a choice between options, including a choice *not* to do something. Notes are short, written when the decision is made, and not rewritten afterwards. If a later decision reverses one, write a new note and link the old one from it.

**What goes where:**
- a **decision note** (here): what was chosen and why;
- a **spec** (`docs/superpowers/specs/`): the design of something to build;
- a **follow-up** ([roadmap.md](../roadmap.md)): work still to do;
- a **[shipped-log.md](../shipped-log.md) row**: what was built.

A decision that leads to building something usually gets a note here *and* a spec. The note says why; the spec says how.

## File name

`YYYY-MM-DD-<short-slug>.md`, dated the day the decision was made. For example: `2026-10-28-vm-chat-budget-back-to-default.md`.

## Format

```markdown
# <The decision, as a short statement>

Date: YYYY-MM-DD · Decided by: <who> · Status: decided | superseded by <link>

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
