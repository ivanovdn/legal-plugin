# Feedback loop — running it and reading it

**Audience:** whoever is running the pilot. The tester-facing half is
[`docs/using-the-assistant.md`](using-the-assistant.md), the *"Telling us what
it got wrong"* section — hand testers that, not this. (It was split out of
`tester-setup.md`, which is now install steps only.)

**What exists:** three Postgres tables in `app-db`. `feedback` holds what an
attorney wrote plus a replayable snapshot of the input; `interaction_event`
holds every Apply, Discard, failure and per-turn counter, recorded silently.
Both are keyed to a `turn_id` minted per request, so any row resolves to the
exact prompt that produced it. `turn_log` holds one row per request to
`/api/query`, its resume and `/api/compact` — failures included — with what
grounded the answer, where the time went and which Word and build sent it.

**What it is for:** six roadmap items are blocked on rates nobody has measured.
Complaints alone can never produce a rate — they are a numerator. The event log
is the denominator, and it was being generated and thrown away.

---

## Read it back

Locally:

```bash
uv run python -m scripts.feedback_report                    # everything, all time
uv run python -m scripts.feedback_report --days 7           # this week
uv run python -m scripts.feedback_report --attorney <id>    # one tester
uv run python -m scripts.feedback_report --document <id>    # one contract
```

**On the VM there is no `uv`** — the backend image installs with plain pip, and
`app-db` publishes only `127.0.0.1:5434`, so this cannot be run from your own
machine either. Use a throwaway container off the same image:

```bash
DC="docker compose -f docker-compose.yml -f docker-compose.remote.yml"
$DC run --rm --no-deps backend python -m scripts.feedback_report --days 7
```

`run --rm` rather than `exec` so a long report can't be tied to the serving
container's lifetime; `--no-deps` so reading the data never restarts anything.
`scripts/` is in the image (`.dockerignore` excludes `tests/`, `docs/` and
`clients/`, not `scripts/`).

Unfiltered it is cumulative for all time, so pilot week one and week ten look
identical. `--days` is what makes a trend visible.

The output has four sections, and the middle two must never be read against
each other — see the warning below.

---

## A session, turn by turn

`--turns` prints `turn_log` as a timeline, oldest first:

```bash
uv run python -m scripts.feedback_report --turns --attorney <id> --days 2
$DC run --rm --no-deps backend python -m scripts.feedback_report --turns --days 1   # on the VM
```

`--turns` shows the newest 50 turns by default, and says so under the heading when it stops there; `--limit N` shows more.

Each entry reads top to bottom: what happened (task, outcome, reason codes, the
error if there was one) · where the time went (model reload, prompt reading,
generation, tokens) · what grounded it (playbook, MSA, how much of the document
was sent) · what the attorney did with it (edits applied / failed / discarded,
flags — applied and failed also count the Findings tab's redlines) · what sent
it (Word platform and version, pane build, backend build — `⚠ stale pane` when
the two builds differ) · what it ran on (`model <name> <digest> · ollama <version>`,
then `⚠ settings changed: <setting> old→new` or `⚠ model weights changed` when
that differs from the turn printed before it) · the question, the `turn_id`, and
the `trace_id` to open in Phoenix.

**What a turn ran on** (since 2026-10-15): every row keeps the backend's settings
in `settings` (JSON — everything in `.env` except credentials), and the model
server's `model_digest` (the exact weights behind e.g. `qwen3.6:latest`) and
`ollama_version` (read at most once a minute, so a model pulled on the server
appears on turns up to a minute later). Neither is covered by `backend_build`: `.env` changes on a
restart, and Spark's model can be re-pulled by the other team sharing it. Rows
from before then have `{}` and empty strings, and are compared with nothing.
Read the backend log for one turn with the first 8 characters of its `turn_id`:
`$DC logs backend | grep 'turn=4b81dc16'` (every app log line carries it).

Ready queries — paste into `$DC exec -T app-db psql -U legal -d legal -P pager=off`:

```sql
-- When a setting or the model changed, and what each turn ran with
SELECT timestamp, task_type, settings->>'ollama_think_review' AS think,
       settings->>'ollama_num_ctx' AS num_ctx, left(model_digest, 12) AS model, ollama_version
FROM turn_log ORDER BY id DESC LIMIT 20;

-- Attorneys whose Word still runs an older pane than the backend
SELECT attorney_id, max(timestamp) AS last_seen, pane_build, backend_build
FROM turn_log WHERE pane_build <> '' AND pane_build <> backend_build
GROUP BY attorney_id, pane_build, backend_build ORDER BY last_seen DESC;

-- Turns that waited on a model reload (Spark switching between its clients)
SELECT timestamp, attorney_id, task_type, duration_ms, llm_load_ms
FROM turn_log WHERE llm_load_ms > 1000 ORDER BY llm_load_ms DESC LIMIT 20;

-- SOW turns answered without a governing MSA
SELECT timestamp, attorney_id, document_id, degradations
FROM turn_log WHERE contract_type = 'sow' AND msa_title = '' ORDER BY id DESC;

-- Failed turns and what broke
SELECT timestamp, attorney_id, kind, task_type, error
FROM turn_log WHERE outcome = 'failed' ORDER BY id DESC LIMIT 20;

-- Which Word the testers use
SELECT office_platform, office_version, count(*) AS turns,
       count(DISTINCT attorney_id) AS attorneys
FROM turn_log GROUP BY 1, 2 ORDER BY turns DESC;
```

**Reading it right:**
- `timestamp` is when the request **arrived**; every other table stamps when it wrote.
- `pane_build = ''` is a pane built before the turn_log deploy (or Chainlit) — unknown, not stale. Panes pick up a new bundle the next time they open.
- `outcome` is the very value stamped on the trace as `app.outcome`; `degradations` are the reason codes from `observability/degradations.py`.
- `duration_ms` is measured in the request handler, `audit_log.duration_ms` in the graph's last node — close, not equal.
- A row is written after the response. If app-db was down at that moment, the row is in the backend log instead: `$DC logs backend | grep "TURN LOG WRITE FAILED"`.
- Turns from before the turn_log deploy have no row.
- A review turn shows `0 proposed`: `edits_proposed` counts chat edit cards only. Its redline Apply and failure events (`redline_applied`, `redline_failed`) still land in the applied / failed counts, because those counts add up events from both tabs. The findings themselves are counted by the pane's `findings_rendered` event, not in `turn_log`.

---

## Pane use, day by day

```bash
uv run python -m scripts.feedback_report --activity --days 7
```

One entry per attorney per UTC day:

```
  2026-10-15  A. Tester (cc91e640-…)   opens 3 · chat 2 · reviews 1 · finalize 0 · errors 1
    waited in Word: chat 9.8s · review 96.1s   server: chat 7.2s · review 88.0s   (medians)
    pane 1a2b3c4 · backend 1a2b3c4
```

- **opens** counts `pane_opened` — a day with opens and `chat 0 · reviews 0` is someone who looked and asked nothing, the case no turn table can show.
- **waited in Word** is measured in the pane from pressing Send / Review to the result on screen; **server** is `turn_log.duration_ms`. The gap is reading the document out of Word, the network and Caddy.
- Opens, waits, tabs and errors exist only from panes built after SP3 (2026-10-14); a day from an older pane shows its turns with `opens 0` and builds `—`.
- `--document` narrows by document id, and a pane opened on a document that never got one (never reviewed or chatted) has none — leave `--document` off when counting opens.
- **finalize** counts `finalize_applied` only; a failed Finalize is in the timeline (`--turns`), not in this count.
- The **waited** medians include requests that failed — a fast error or a long timeout moves them; check `errors` on the same line.
- `⚠ stale pane` on the build line means the pane's build differs from the backend's that day: someone is running an older pane.
- A review refused before it was sent (document empty or too short) is a `client_error` with no turn.

The pane's events, all `interaction_event` rows with `surface = 'pane'`:

| `action` | When | `target_ref` | `detail` |
|---|---|---|---|
| `pane_opened` | each pane load | — | `saved` / `unsaved` / `""` |
| `tab_viewed` | each tab switch | `findings` / `chat` / `preferences` | — |
| `finalize_applied` / `finalize_failed` | after the in-pane confirm | — | the error, on failure |
| `client_error` | an error the pane showed, or an uncaught one | `chat` / `review` / `compact` / `preferences` / `feedback` / `window` | the message |
| `turn_waited` | a chat answer or review on screen, or failed | `chat` / `review` | milliseconds |

Uncaught pane errors (`window`) are de-duplicated and capped at 20 per pane load.

`--turns` now shows each turn's `(waited X in Word)` and prints the session's turn-less pane events (opened, tab, Finalize, errors) between the turns. Requests that never reached the backend are in Caddy's files — [deploy-vm.md](deploy-vm.md), *Logs*.

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

⚠️ **`finding_commented` does NOT mean the attorney commented on a finding.**
It fires when the add-in inserts a Word comment into the document — the
*Show in document* button ([FindingCard.tsx](../clients/word/src/components/FindingCard.tsx)),
not the ⚑ flag. A flagged finding writes a `feedback` row and no
`interaction_event` at all, so zero here alongside real feedback rows is
correct, not a dropped write. Verified on the VM 2026-08-19.


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
- **What the attorney waited, measured in Word, and what the pane did between
  turns** (opened, tabs viewed, Finalize). `turn_log.duration_ms` is the
  server's view. Planned as SP3 (pane lifecycle events).
- **Requests rejected before a handler runs** — a malformed request (422), an
  SSO 401 once SSO is on — leave no row. Planned as SP3 (Caddy access log).

---

## Health check

Both endpoints are designed to fail in opposite directions, and it is worth
confirming after any deploy.

**On the VM, `localhost:8000` does not exist** — `docker-compose.remote.yml`
publishes no host port for the backend, so `/api/*` is reachable only through
Caddy. Set `BASE` for wherever you are and the rest is identical:

```bash
# local
BASE="http://localhost:8000"; CURL="curl -s"

# VM (from the VM itself; --resolve avoids depending on its own /etc/hosts)
HOST=$(grep -E '^ADDIN_ORIGIN_HOST=' .env | cut -d= -f2)
BASE="https://$HOST"; CURL="curl -sk --resolve $HOST:443:127.0.0.1"
```

```bash
# quiet: telemetry must never break an Apply
$CURL -X POST "$BASE/api/events" -H 'Content-Type: application/json' \
  -H 'X-User-ID: probe' -d '{"events":[{"action":"probe","surface":"probe"}]}'
# -> 200 {"recorded":1}   (and 200 {"recorded":0} with app-db down)

# loud: the attorney was shown "Sent ✓", so a lost report is a lie
$CURL -o /dev/null -w '%{http_code}\n' -X POST "$BASE/api/feedback" \
  -H 'Content-Type: application/json' -H 'X-User-ID: probe' -d '{"comment":"probe"}'
# -> 200   (and 500 with app-db down, writing nothing)

docker compose exec -T app-db psql -U legal -d legal \
  -c "DELETE FROM feedback WHERE attorney_id='probe';" \
  -c "DELETE FROM interaction_event WHERE attorney_id='probe';"
```

Delete the probe rows even on a green run — they are indistinguishable from
real tester rows in the report, and `attorney_id='probe'` is the only thing
that separates them.

**On timing.** With `app-db` down both calls now settle within `config.db_pool_timeout`
(3.0s), and return instantly when the pool is already holding broken connections.
Before that bound they inherited `psycopg_pool`'s 30s default — measured 30.00s
against an unreachable host, versus 3.00s after. If a probe hangs appreciably
longer than 3s, the setting is not reaching the pool; check for a stale backend
(`uvicorn` does not reload config changes) rather than assuming Postgres is slow.

Design rationale, including why events carry short contract excerpts and why
there are two tables rather than one:
[`docs/superpowers/specs/2026-08-14-tester-feedback-capture-design.md`](superpowers/specs/2026-08-14-tester-feedback-capture-design.md).
