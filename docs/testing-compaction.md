# Testing context compaction by hand

## What compaction is actually for

Read the rest of this document in chars and percentages and it looks like we are
fighting a capacity limit. We are not. qwen3.6's window is **131,072 tokens**;
`chat_context_max_chars` is 150,000 characters ≈ **30,700 tokens — 23% of it**.
There is 4x headroom, and there always has been since `ollama_num_ctx` went to
131,072.

The budget is a **latency ceiling**, derived in `config.py` from a 30-second turn:
prefill runs at 1,397 tok/s on Spark, an answer costs ~7.8s of decode, so ~31,000
tokens of prompt is what fits in the remainder. Compaction buys back **prefill
seconds**, and it protects the contract because the document is what
`_cap_chat_context` cuts when the budget is exceeded.

Two consequences worth holding onto while testing:

- **Raising the budget is a real option, not a capacity violation.** It costs
  latency, nothing else. KV is cheap here — ~49.5 MB per 1k tokens, because
  qwen3.6 is a hybrid SSM/attention MoE with only ~10 full-attention layers of 40.
- **Latency measured locally does not transfer.** 1,397 tok/s is Spark. Local Mac
  turns ran 37–78s on 2026-08-27 where the arithmetic predicts ~13s. Judge budget
  and latency changes on the VM.

## The manual gate

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
| `compressible_chars >= compaction_auto_min_chars` (4,000) | ~2 prose turns past the last segment, since the newest 2 rows stay verbatim |

There is deliberately **no message-count condition**. A count cannot guard a size
budget — that error was made three times running, and the third time shipped a
floor that could not arm until the contract had already been cut.

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

## Reading the decision

Every doc-chat turn logs why auto-compaction did or did not fire, whether or
not anything happened:

```bash
# local — start.sh logs to its own terminal, so pipe it when you start it:
bash scripts/start.sh 2>&1 | tee /tmp/backend.log
grep -i compaction /tmp/backend.log
# VM
docker compose -f docker-compose.yml -f docker-compose.remote.yml \
  logs backend --since 30m | grep -i compaction
```

Captured locally on 2026-08-27, a grounded MSA conversation at
`CHAT_CONTEXT_MAX_CHARS=90000`, on the code that still had the damage-first
floors:

```
16:02:25  auto=False can=False pct=87/90 compressible=0 msgs/0 chars     floors=6 msgs/20000 chars
16:05:17  auto=False can=False pct=96/90 compressible=0 msgs/0 chars     floors=6 msgs/20000 chars
16:06:26  auto=False can=True  pct=99/90 compressible=2 msgs/8642 chars  floors=6 msgs/20000 chars
16:07:27  WARNING chat context 90443 > budget 90000 — truncated document to 15524 chars
16:07:27  auto=False can=True  pct=99/90 compressible=4 msgs/12485 chars floors=6 msgs/20000 chars
```

Confirmed live on the fixed code at 16:26, same document and same questions:

```
16:26:04  auto=True can=True pct=99/90 compressible=2 msgs/8642 chars floor=4000 truncated=False
16:26:55  [compaction] condensed rows 237-240 (4 messages) into 5 quotes (8 dropped),
          freeing 9778 of 8173 chars requested
```

It arms one turn earlier than the old floors ever could, `truncated=False`, and
the cut never happens: history 12,022 → 1,085, document untouched at 16,008, no
red notice. Note the two off-by-ones, both correct: the breakdown says 2
compressible messages because it is measured at REQUEST time, and the run 51
seconds later condenses 4 because that turn's own rows have since been stored.

Read it left to right: `pct` against the warn line says whether there is
pressure, `compressible` against `floor` says whether there is anything worth
condensing, `truncated` says whether contract text was lost on this turn, and
`auto` is the verdict the pane was actually sent. The first two turns are
correctly silent — the keep-recent floor leaves nothing compressible until a
third turn exists.

**`truncated=True` with `auto=False` is the alarm.** It means contract text was
dropped on a turn where the system declined to condense, which is the exact
failure the feature exists to prevent. Before the floors were fixed this was the
NORMAL state: on 2026-08-27 the turn that cut 484 characters of the document
logged `auto=False can=True pct=99/90 compressible=4 msgs/12485 chars` against a
20,000-char floor that history could not have reached without losing another
8,300 characters of contract first.

The line is what separates the two ways this feature fails:

| you see | it means |
|---|---|
| `auto=False`, `compressible` under the floor, `truncated=False` | working as designed |
| `auto=False`, `truncated=True` | **the alarm** — contract text lost on a turn that declined to condense |
| `auto=False`, `compressible` over the floor | a backend bug: `can_compact`, `enabled` or `auto_cfg` will say why |
| `auto=True`, then `[compaction] condensed rows N-M` | the whole path worked |
| `auto=True`, then **nothing** | the PANE dropped it — almost always the disarm latch, cleared by reloading the task pane |

Note the last row: the pane disarms auto for its lifetime after one failed or
refused automatic run, and from the server that looks identical to a pane that
never tried. This line is the only place the difference is visible.

## The six checks

| # | Do | Pass |
|---|---|---|
| 1 | Cross the warn line | Exactly **one** `[compaction] condensed rows N-M` per triggering turn — never a burst |
| 2 | Send a **grounded** message right after an auto run | **Nothing fires.** The churn loop the floor exists to prevent |
| 3 | Watch during and after | "Condensing earlier turns automatically… (10–30 s)", then a one-line headline. No Condense button beside it |
| 4 | Send a message *while* a run is in flight | The turn answers; the finished run's headline is still readable when it lands |
| 5 | Stop Ollama, force a trigger | Error renders **once**, says "automatically", never reappears; manual button still works |
| 6 | `COMPACTION_AUTO=false`, restart | Nothing auto-fires; the manual button is unchanged |
| 7 | Force an automatic failure, then send a grounded message | The red error is **gone**. It expires with its turn, like the success note |
| 8 | After that failure, condense **manually**, then send a grounded message | Auto fires again. A successful run re-arms it |

**#2 must use a grounded question.** Follow an auto run with *"who signs this?"*
and grounding detaches, `pct` falls under the warn line, and nothing fires
because the *pressure* vanished — not because the floor held. That is a vacuous
pass on the most important check.

**Checks 7 and 8 exist because an automatic failure used to leave the pane in a
state only a reload could clear.** Both were found by sideload on 2026-09-10 and
neither is unit-testable — the add-in has no React test harness. They share one
root: two behaviours that were individually correct and lethal together.

- The error notice had no turn scoping, and `setError(null)` ran only at the
  start of the *next* run. An automatic failure also disarms auto, so there was
  no next run: the red line stayed for the life of the pane. Measured — a failure
  at 16:04 was still on screen at 16:08, through a completed turn.
- `setDisarmed(false)` did not exist. One transient Ollama blip disabled
  automatic compaction for the rest of the session, even after a manual run had
  proved the model was reachable again. The only cure was reloading a task pane,
  which no attorney would know to do.

Reading check 8 in the log: after the manual condense, the next qualifying turn
must show `auto=True` **followed by** `[compaction] condensed rows N-M`. `auto=True`
with nothing after it means the latch is still holding and the fix regressed.

## Gotchas that cost time on the first run

**`compressible_history` is scoped to `(document_id, attorney_id)`.**
Switching documents mid-test restarts the count: a document you have not
chatted on has no segments and its own floor. To check the live figure, use the
real function rather than SQL — a hand-written `max(to_id)` across the whole
table is wrong and will mispredict:

```python
from skills.legal_research.context import compressible_history
compressible_history({"document_id": DOC, "user_id": ATTY})   # -> (messages, chars)
```

**But prefer the log to any query run after the fact.** A store query answers
"what is compressible *now*", which is a different question from "what was
compressible when the turn fired" — and a manual Condense in between advances
`latest_to_id`, so the query legitimately returns `(0, 0)` no matter what the
turn saw. Reading the decision off the log avoids the trap entirely.

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

From 2026-08-26, one document, six prose turns (manual button):

```
history   11,013 chars (12%)  ->  1,361 (1%)
freed     9,652 characters
segment 6 covers rows 189-196, 1,361 chars standing in for 11,013
7 of 14 quotes dropped — all from the two most table-shaped rows
```

Before that, the floor was observed holding twice at `compressible = 4`: the
manual button offered, auto deliberately silent.

And the first automatic run to prevent a truncation, 2026-08-27, grounded SOW +
MSA at a 90,000 budget:

```
fired at   pct=99, compressible 2 msgs / 8,642 chars
condensed  rows 237-240 (4 messages) -> 5 quotes, 8 dropped
freed      9,778 chars against a target of 8,173
history    12,022 -> 1,085 (13% -> 1%)
document   16,008, never cut
```

Two things worth knowing from that run. **8 of 13 quotes dropped, every one from
a single row** — a list-shaped answer, the same structural unquotability as a
markdown table. Its space was still reclaimed, because a segment replaces its
whole range regardless. And **after condensing, the turn sat at 88%** — one
point under the warn line, because fixed content (document + playbook + MSA +
system) is 87% of a 90,000 budget before a single message of history. At that
budget auto will re-fire roughly every two turns. That is the budget being
small, not compaction misbehaving; the shipped 150,000 puts the same turn at
52%.
