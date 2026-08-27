# Testing context compaction by hand

The auto-fire effect has **no automated coverage and cannot have any**: the Word
add-in has no React test harness — its tests are plain `tsx` assertion scripts
that can neither render a component nor drive a `useEffect`. `scripts/check.sh`
covers the arithmetic on both sides of the seam and nothing else. This document
is the rest of the gate.

Everything below was executed against Word for Mac on 2026-08-26; the measured
figures are from that run.

## Setting up

```bash
# 1. The budget must be lowered or NOTHING will fire.
#    In .env:  CHAT_CONTEXT_MAX_CHARS=90000     (production default is 150000)
# 2. Restart — compaction_* is @lru_cache'd in get_settings.
bash scripts/start.sh
# 3. Separate terminal:
cd clients/word && npm run dev
```

Then reload the task pane in Word.

**Why the budget has to move.** A grounded SOW turn measures ~87k chars. Against
the shipped 150,000 that is 58% — nowhere near the 90% warn line, so
`can_compact` is false and neither the button nor auto ever appears. At 90,000
the same turn is 96–98%. Put it back to `150000` when you are done.

## Reaching the trigger

Two conditions, both required:

| condition | how to satisfy it |
|---|---|
| `pct >= compaction_warn_pct` (90) | ask **grounded** questions — see below |
| `compressible_messages >= compaction_auto_min_messages` (6) | 8 stored messages past the last segment = **4 turns** |

### Grounded vs lean questions

`_needs_grounding` (`skills/legal_research/context.py`) keys off the question's
*wording*. A lean question detaches the playbook and MSA and drops the context
by ~55k chars, so it cannot reach the warn line. Verified against the real
function:

**Grounded** — any clause name, firm-position word, or edit verb:
- Can we soften the indemnity cap?
- Is the termination notice period standard?
- Should we push back on the payment terms?
- Does this conflict with the governing MSA?
- Are the SLA credits aggressive?
- Is this a deviation from the playbook?

**Lean — these will NOT move the counter**, despite sounding substantive:
- Who signs this document?
- What is the billing model?
- When does this contract start?
- Summarise this document for me.
- What are the key dates?

Careful with the near-misses: *"List every Missing Context item and explain why
each one blocks signature"* is **lean** — neither "Missing Context" nor "blocks
signature" is a trigger. Adding "unacceptable" or "risk" makes it grounded.

### Growing history enough to matter

`history_chars` is measured **after** `_sanitize_history`, which strips fenced
blocks from assistant turns and leaves user turns byte-identical. So:

- your own messages count in full,
- assistant **prose** counts,
- assistant **edit blocks do not**.

A session of *"redline this"* turns barely grows history at all. Measured: an
edit-heavy run condensed 8 messages for **1,598 chars**; the prose-heavy run
below condensed 8 messages for **9,652**.

Six turns that accumulate properly (all verified grounded). Ask for analysis,
not edits:

1. Walk me through every risk in this SOW clause by clause, and tell me which ones are Red.
2. Compare the termination provisions in this SOW against the governing MSA and explain any conflict.
3. What is our standard position on indemnity, the liability cap, and IP ownership? Explain each.
4. List every Missing Context item and explain why each one is unacceptable to sign as drafted.
5. The counterparty pushed back on three points: they want the liability cap at 6 months of fees, they will not accept the mutual non-solicit, and they want to strike the audit right. Take each in turn and tell me where we can move and where we cannot.
6. Explain how the confidentiality obligations here interact with the IP ownership clause, and whether the combination protects us on residual knowledge.

## The six checks

| # | Do | Pass |
|---|---|---|
| 1 | Cross the warn line | Exactly **one** `[compaction] condensed rows N-M` per triggering turn — never a burst |
| 2 | Send a **grounded** message right after an auto run | **Nothing fires.** The churn loop the floor exists to prevent |
| 3 | Watch during and after | "Condensing earlier turns automatically… (10–30 s)", then a one-line headline. No Condense button beside it |
| 4 | Send a message *while* a run is in flight | The turn answers; the finished run's headline is still readable when it lands |
| 5 | Stop Ollama, force a trigger | Error renders **once**, says "automatically", never reappears; manual button still works |
| 6 | `COMPACTION_AUTO=false`, restart | Nothing auto-fires; the manual button is unchanged |

**#2 must use a grounded question.** Follow an auto run with *"who signs this?"*
and grounding detaches, `pct` falls under the warn line, and nothing fires
because the *pressure* vanished — not because the floor held. That is a vacuous
pass on the most important check.

## Gotchas that cost time on the first run

**`compressible_message_count` is scoped to `(document_id, attorney_id)`.**
Switching documents mid-test restarts the count: a document you have not
chatted on has no segments and its own floor. To check the live figure, use the
real function rather than SQL — a hand-written `max(to_id)` across the whole
table is wrong and will mispredict:

```python
from skills.legal_research.context import compressible_message_count
compressible_message_count({"document_id": DOC, "user_id": ATTY})
```

**The breakdown is measured at REQUEST time.** The counter you are looking at
describes the turn that produced it, before that turn's own two rows were
stored. So the fire happens on the turn *after* the one where the arithmetic
first reaches the floor. Expect an off-by-one and do not read it as a bug.

**Markdown tables are structurally unquotable.** Measured: an 8,355-char
table-shaped answer (324 pipes) offered the gate **8** quotable sentence
boundaries in the whole row and contributed **zero** quotes to its segment — a
pipe-delimited row has no sentence terminator followed by a capital. A drop
count around 50% on a table-heavy conversation is expected and correct, not a
regression; the ~9% per-quote failure baseline was measured on prose. Note the
consequence: the row's space is still reclaimed, because a segment replaces its
whole range regardless of how much got quoted — so the largest answer in a
conversation can end up with no representation in the summary at all.

**Do not paste a large document into the chat box to give the session an MSA.**
It lands in `system & question`, which `_cap_chat_context` never truncates and
compaction can never touch — so the *attached* document is cut instead, to zero
if the paste is big enough, and the model answers having read none of the
contract. The paste is then stored and replayed as history on every later turn,
so it keeps costing you the document until it is condensed. Measured on the VM
2026-08-27: `system & question` 92,999 chars (103% of budget on its own),
document 16,008 → 0. Real MSA grounding comes only from
`scripts/ingest_demo_msa.py`, which puts it in Qdrant as `doc_type="msa"` where
`attach_parent_msa` can find it — that is what makes the `governing MSA` row
appear in the counter.

**Without the MSA ingested, the budget has to come down further.** Fixed content
on a VM with no MSA is document + playbook + system ≈ 53,500 chars, so at
`CHAT_CONTEXT_MAX_CHARS=90000` a turn sits at ~59% and nothing ever fires. The
arithmetic: auto fires when history ≥ `0.9 × budget − fixed`, and the document
starts truncating when history > `budget − fixed`. At 68,000 that is a fire at
7,673 chars of history with truncation not starting until 14,473 — a workable
gap. Prefer ingesting the MSA and testing at 90,000, which is the configuration
you actually ship.

## What a good run looks like

From 2026-08-26, one document, six prose turns:

```
history   11,013 chars (12%)  ->  1,361 (1%)
freed     9,652 characters
segment 6 covers rows 189-196, 1,361 chars standing in for 11,013
7 of 14 quotes dropped — all from the two most table-shaped rows
```

Before that, the floor was observed holding twice at `compressible = 4`: the
manual button offered, auto deliberately silent.
