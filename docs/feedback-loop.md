# Feedback loop — running it and reading it

**Audience:** whoever is running the pilot. The tester-facing half is
[`docs/tester-setup.md`](tester-setup.md) step 7 — hand testers that, not this.

**What exists:** two Postgres tables in `app-db`. `feedback` holds what an
attorney wrote plus a replayable snapshot of the input; `interaction_event`
holds every Apply, Discard, failure and per-turn counter, recorded silently.
Both are keyed to a `turn_id` minted per request, so any row resolves to the
exact prompt that produced it.

**What it is for:** six roadmap items are blocked on rates nobody has measured.
Complaints alone can never produce a rate — they are a numerator. The event log
is the denominator, and it was being generated and thrown away.

---

## Read it back

```bash
uv run python -m scripts.feedback_report                    # everything, all time
uv run python -m scripts.feedback_report --days 7           # this week
uv run python -m scripts.feedback_report --attorney <id>    # one tester
uv run python -m scripts.feedback_report --document <id>    # one contract
```

Unfiltered it is cumulative for all time, so pilot week one and week ten look
identical. `--days` is what makes a trend visible.

The output has four sections, and the middle two must never be read against
each other — see the warning below.

---

## From a complaint to the prompt that caused it

Every feedback row prints a `turn:` id and, when tracing is on, a `trace:` id.

1. `turn_id` is the join key. It is always present, minted per request,
   independent of whether tracing is running.
2. `trace_id` is the 32-hex OTel id — paste it into Langfuse (local) or Phoenix
   (VM) and you get the verbatim prompt, the assembled grounding, the model
   reply and the latency for that turn.
3. The full snapshot is not printed (it is large). Pull it with the row id:

```sql
SELECT snapshot->>'document_text' FROM feedback WHERE id = 7;
SELECT jsonb_pretty(snapshot->'target') FROM feedback WHERE id = 7;
```

Verified live 2026-08-17: the `turn_id` on a feedback row matched the `turn_id`
in that trace's metadata byte-for-byte, in both directions.

---

## ⚠️ The one way to misread this

`interaction_event` holds two different kinds of row:

| Kind | Fires | Magnitude |
|---|---|---|
| **Counter** — `edits_proposed`, `findings_rendered`, `preferences_suggested` | once per **turn/review** | in `detail` |
| **Per-item** — `edit_applied`, `edit_discarded`, `edit_failed`, `redline_*`, `finding_commented`, `preference_added`, `*_jump_notfound` | once per **item** | one row each |

A raw `COUNT(*)` over both is how "12 `edits_proposed`, 19 `edit_applied`" reads
as a **158% apply rate** instead of 19-of-37. The report prints them as two
labelled sections for exactly this reason. **Never divide a number in one
section by a number in the other** — use the counter's `total` (the summed
magnitude), never its `turns`.

---

## The rates worth watching

Once there is volume — and only then:

| Rate | Numerator ÷ denominator | Tells you |
|---|---|---|
| **Discard rate** | `edit_discarded` ÷ `edits_proposed` *total* | how often a proposed edit is unwanted |
| **Matcher miss rate** | `edit_failed` ÷ (`edit_applied` + `edit_failed`) | whether `body.search` actually finds its target in real documents |
| **Finding engagement** | `finding_commented` ÷ `findings_rendered` *total* | whether findings are actionable enough to act on |
| **Spurious proposal rate** | see below | whether we propose edits on questions that asked for none |

`edit_failed` carries the matcher's own error string in `detail`. That is the
only field measurement this project has ever had of `findClauseRange`,
`searchCandidates`, the 85% completeness guard, wildcard escaping and
tab-segment reduction — all of which were built against single traces.

### The spurious-proposal surface

The last section of the report is one row per chat turn that proposed edits:

```
2026-08-18T08:53:11  proposed=1 applied=0 discarded=0 failed=1
  asked: change the governing law to England and Wales
  turn: c0b93f00-...
```

**A question that reads as purely factual with `proposed > 0` is the bug
reproducing.** `applied`/`discarded` are the attorney's verdict on each card.
Scanning this list is the closest thing to a rate that exists before a taxonomy
does, and it needs no trace lookup — which is what made it computable at all.

---

## What this cannot answer yet

- **No taxonomy.** Comments are free text by design; categories should be
  derived from the first ~30 real items, not guessed in advance.
- **Snapshots aren't in the report.** You get the row id and go to `psql`.
- **No per-attorney or per-document breakdown**, only filters. Fine for a small
  pilot; a `GROUP BY` would be needed for a large one.
- **Test flags look like real ones.** Deliberate — real testers report problems
  rather than testing the reporter. Revisit if the first cohort proves otherwise.
- **Unsaved documents orphan their rows.** Word cannot persist the document id
  on an unsaved file, so closing one loses the link between its feedback and any
  later feedback on the same contract. Open bug; tell testers to save first.

---

## Health check

Both endpoints are designed to fail in opposite directions, and it is worth
confirming after any deploy:

```bash
# quiet: telemetry must never break an Apply
curl -s -X POST localhost:8000/api/events -H 'Content-Type: application/json' \
  -H 'X-User-ID: probe' -d '{"events":[{"action":"probe","surface":"probe"}]}'
# -> 200 {"recorded":1}   (and 200 {"recorded":0} with app-db down, in ~2ms)

# loud: the attorney was shown "Sent ✓", so a lost report is a lie
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/api/feedback \
  -H 'Content-Type: application/json' -H 'X-User-ID: probe' -d '{"comment":"probe"}'
# -> 200   (and 500 with app-db down, writing nothing)

docker compose exec -T app-db psql -U legal -d legal \
  -c "DELETE FROM feedback WHERE attorney_id='probe';" \
  -c "DELETE FROM interaction_event WHERE attorney_id='probe';"
```

Design rationale, including why events carry short contract excerpts and why
there are two tables rather than one:
[`docs/superpowers/specs/2026-08-14-tester-feedback-capture-design.md`](superpowers/specs/2026-08-14-tester-feedback-capture-design.md).
