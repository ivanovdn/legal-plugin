# Automatic Compaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fire the existing compaction call automatically when the context budget is under pressure, instead of waiting for an attorney to notice an amber line and press a button.

**Architecture:** The decision is computed on the backend as a new `auto_compact` field on the context breakdown, alongside the existing `can_compact`, so switching it on the VM is a `start.sh` restart rather than a rebuild and re-sideload of the add-in. The Word pane's `ContextMeter` reuses its existing compaction flow verbatim and adds an effect that fires it. Nothing in `skills/legal_research/compaction.py`, the validation gate, the segment format, or `POST /api/compact` changes.

**Tech Stack:** Python 3.12 + pydantic-settings (`config.py`), FastAPI, pytest; React 18 + TypeScript + Vite for the Office.js task pane; plain `tsx` assertion scripts for add-in tests (no React test harness exists).

**Spec:** [docs/superpowers/specs/2026-08-26-automatic-compaction-design.md](../specs/2026-08-26-automatic-compaction-design.md)

## Global Constraints

- **All imports at the top of the file.** No lazy imports inside functions. (CLAUDE.md hard rule 1.)
- **No backwards-compat shims.** When a signature changes, change the call sites. (CLAUDE.md hard rule 5.)
- **Do not modify `skills/legal_research/compaction.py`, `api/routes/compact.py`, `evals/`, or any `evals/cases/*.json`.** This slice changes the trigger, not the mechanism. A diff touching those files is out of scope.
- **`compaction_auto` defaults to `True`; `compaction_auto_min_messages` defaults to `6`.** Exact values, from the spec.
- **`auto_compact` may only ever be a narrowing of `can_compact`, never a widening.** `auto_compact == True` while `can_compact == False` is a defect in every code path.
- **New config is `@lru_cache`'d in `get_settings`** like every other `compaction_*` field, so tests that change it must call `get_settings.cache_clear()` on the way **out** as well as in — see Task 1 Step 1.
- **Backend tests require Docker.** `tests/conftest.py` starts an ephemeral Postgres via testcontainers.
- **`bash scripts/check.sh` is the only gate.** There is no CI. It must be green before the final commit of every task.
- **Run `uv run pytest`, never bare `pytest`.**

---

## File Structure

| file | responsibility | task |
|---|---|---|
| `config.py` | the two new settings and the reasoning for the floor | 1 |
| `skills/legal_research/context.py` | `auto_compact` composed from `can_compact` + the two settings | 1 |
| `tests/test_context_breakdown.py` | the threshold band, the master switch, the narrowing invariant | 1 |
| `clients/word/src/contextGauge.ts` | `auto_compact` on the interface; narrowed in `withLiveDocument` | 2 |
| `clients/word/src/contextGauge.test.ts` | assertions that the recompute narrows and never widens | 2 |
| `scripts/check.sh` | `EXPECTED_PASS_COUNT` bumped for the new assertions | 2 |
| `clients/word/src/components/ContextMeter.tsx` | owns the live-document adjustment; fires automatically; disarms on failure | 3 |
| `clients/word/src/App.tsx` | forwards `liveDocChars`/`docTruncated` instead of pre-adjusting | 3 |
| `CLAUDE.md`, `docs/wiki.md` | the gotchas and the shipped record | 4 |

---

### Task 1: Backend — `auto_compact` on the breakdown

**Files:**
- Modify: `config.py` (the compaction block, immediately after `compaction_max_injected_segments`)
- Modify: `skills/legal_research/context.py::build_context_breakdown`
- Test: `tests/test_context_breakdown.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `build_context_breakdown(...)` returns a dict that now contains the key `"auto_compact": bool`, alongside the existing `"can_compact": bool` and `"compressible_messages": int`. Two new settings on `Settings`: `compaction_auto: bool` and `compaction_auto_min_messages: int`. Task 2 mirrors `auto_compact` onto the TypeScript `ContextBreakdown` interface.

- [ ] **Step 1: Add a settings fixture that cleans up after itself**

Append to `tests/test_context_breakdown.py`. This fixture exists because `get_settings` is `@lru_cache`'d: clearing only on the way *in* leaves the polluted `Settings` object cached for every test that runs afterwards and does not clear it itself.

```python
@pytest.fixture
def settings_env(monkeypatch):
    """Set config via env for ONE test, then restore the cached Settings.

    get_settings is @lru_cache'd. Clearing only on the way in would leave this
    test's Settings object cached for every test that follows and does not clear
    it itself — the clear on the way OUT is what keeps the pollution local.
    """
    def _set(**env):
        for key, value in env.items():
            monkeypatch.setenv(key, str(value))
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()
```

Add `import pytest` to the file's imports if it is not already there (it currently is not — the file imports only `skills.legal_research.context as ctx`, `get_settings`, `append_turn`, `append_segment`, and the two functions under test).

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_context_breakdown.py`:

```python
def test_auto_compact_needs_more_history_than_the_button_does():
    """The anti-churn floor, and the most important assertion in this slice.

    After a compaction, compressible history drops to ~0 and grows by exactly two
    rows per turn. If automatic firing shared the button's "> 0" threshold it would
    fire on the very next turn against two short messages — and a segment carries a
    261-char header plus ~20 chars per quote line, so the net-benefit guard would
    decline it. That is a 10-30s LLM call, on a shared Ollama, guaranteed to write
    nothing. So there must be a band where the button is offered and auto stays quiet.
    """
    settings = get_settings()
    b = _bd(
        doc_chars=int(settings.chat_context_max_chars * 0.95),
        compressible_messages=settings.compaction_auto_min_messages - 1,
    )
    assert b["can_compact"] is True
    assert b["auto_compact"] is False


def test_auto_compact_fires_once_the_floor_is_reached():
    settings = get_settings()
    b = _bd(
        doc_chars=int(settings.chat_context_max_chars * 0.95),
        compressible_messages=settings.compaction_auto_min_messages,
    )
    assert b["auto_compact"] is True


def test_auto_compact_is_a_narrowing_of_the_button_never_a_widening():
    """Below the warn line there is no pressure to relieve, however much history
    has piled up. auto_compact true with can_compact false is a defect anywhere."""
    b = _bd(doc_chars=1000, compressible_messages=500)
    assert b["can_compact"] is False
    assert b["auto_compact"] is False


def test_auto_is_armed_by_default():
    """compaction_auto ships ON: the flag exists so a pilot can switch the
    behaviour off after seeing it, not so someone has to switch it on to see it."""
    settings = get_settings()
    assert settings.compaction_auto is True
    b = _bd(
        doc_chars=int(settings.chat_context_max_chars * 0.95),
        compressible_messages=settings.compaction_auto_min_messages + 10,
    )
    assert b["auto_compact"] is True


def test_the_master_switch_stops_auto_without_taking_away_the_button(settings_env):
    """Turning compaction_auto off must leave the manual control exactly as it was —
    the switch governs unrequested firing, not the attorney's own button."""
    settings_env(COMPACTION_AUTO="false")
    settings = get_settings()
    assert settings.compaction_auto is False
    b = _bd(
        doc_chars=int(settings.chat_context_max_chars * 0.95),
        compressible_messages=settings.compaction_auto_min_messages + 10,
    )
    assert b["can_compact"] is True
    assert b["auto_compact"] is False


def test_disabling_compaction_entirely_stops_both(settings_env):
    settings_env(COMPACTION_ENABLED="false")
    settings = get_settings()
    b = _bd(
        doc_chars=int(settings.chat_context_max_chars * 0.95),
        compressible_messages=settings.compaction_auto_min_messages + 10,
    )
    assert b["can_compact"] is False
    assert b["auto_compact"] is False
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_context_breakdown.py -q`
Expected: FAIL — `KeyError: 'auto_compact'` on the first four, and `AttributeError: 'Settings' object has no attribute 'compaction_auto_min_messages'` on the rest.

- [ ] **Step 4: Add the two settings**

In `config.py`, immediately after the line `compaction_max_injected_segments: int = 3`, insert:

```python
    # Fire compaction without a click. Default ON deliberately: the flag exists so a
    # pilot can switch the behaviour off after seeing it, not so someone has to
    # switch it on to see it at all.
    compaction_auto: bool = True
    # How much un-condensed history must pile up before firing UNASKED. This is an
    # anti-churn floor, not a tuning knob. After a compaction, compressible history
    # drops to ~0 and grows by exactly two rows per turn, so sharing can_compact's
    # "> 0" threshold would fire on every subsequent turn against two short
    # messages — and a segment costs a 261-char header plus ~20 chars of
    # "[#id speaker]" per quote line, so that segment is LARGER than its source and
    # the net-benefit guard declines it. The result would be a 10-30s LLM call every
    # turn, on a shared Ollama, guaranteed to write nothing. Six is three turns of
    # accumulation: a round number chosen to clear the two-rows-per-turn growth
    # rate, not a measured optimum. The manual button keeps the can_compact
    # threshold — this governs only what happens without being asked.
    compaction_auto_min_messages: int = 6
```

- [ ] **Step 5: Compose `auto_compact` in the breakdown**

In `skills/legal_research/context.py::build_context_breakdown`, `can_compact` is currently computed inline inside the returned dict literal. Lift it to a local so `auto_compact` can be built from it — that is what makes the narrowing invariant structural rather than a duplicated expression that can drift.

Replace this (the `pct` line through the end of the `return`):

```python
    pct = (total * 100 // budget) if budget else 0
    return {
        "budget_chars": budget,
        "budget_tokens": int(budget / cpt),
        "chars_per_token": cpt,
        "total_chars": total,
        "total_tokens": int(total / cpt),
        "pct": pct,
        "warn_pct": settings.compaction_warn_pct,
        # Both conditions, always. The threshold alone would offer a no-op on a
        # short conversation with a huge document; compressible history alone
        # would nag on every routine chat.
        "can_compact": bool(
            settings.compaction_enabled
            and compressible_messages > 0
            and pct >= settings.compaction_warn_pct
        ),
        "compressible_messages": compressible_messages,
        "parts": parts,
    }
```

with:

```python
    pct = (total * 100 // budget) if budget else 0
    # Both conditions, always. The threshold alone would offer a no-op on a
    # short conversation with a huge document; compressible history alone
    # would nag on every routine chat.
    can_compact = bool(
        settings.compaction_enabled
        and compressible_messages > 0
        and pct >= settings.compaction_warn_pct
    )
    return {
        "budget_chars": budget,
        "budget_tokens": int(budget / cpt),
        "chars_per_token": cpt,
        "total_chars": total,
        "total_tokens": int(total / cpt),
        "pct": pct,
        "warn_pct": settings.compaction_warn_pct,
        "can_compact": can_compact,
        # Built FROM can_compact, not alongside it, so "auto is a narrowing of the
        # button" is structural and cannot drift. Firing unasked needs a higher bar
        # than offering a button: see compaction_auto_min_messages in config.py for
        # the churn loop the floor exists to prevent.
        "auto_compact": bool(
            can_compact
            and settings.compaction_auto
            and compressible_messages >= settings.compaction_auto_min_messages
        ),
        "compressible_messages": compressible_messages,
        "parts": parts,
    }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_context_breakdown.py -q`
Expected: PASS, all tests in the file.

- [ ] **Step 7: Fix a stale comment in the same file**

`tests/test_context_breakdown.py::test_compressible_count_excludes_the_verbatim_window` carries a comment naming the old value of `compaction_keep_recent_messages`. The assertion reads the setting and is correct; only the prose is stale. Replace:

```python
    # 5 turns = 10 messages; the most recent compaction_keep_recent_messages (6)
    # stay verbatim, so only 4 are condensable.
```

with:

```python
    # 5 turns = 10 messages; the most recent compaction_keep_recent_messages (2)
    # stay verbatim, so 8 are condensable. The assertion reads the setting rather
    # than the number, because that floor has already moved once.
```

- [ ] **Step 8: Run the full gate**

Run: `bash scripts/check.sh`
Expected: all checks pass. Backend test count rises from 628 to 634.

- [ ] **Step 9: Commit**

```bash
git add config.py skills/legal_research/context.py tests/test_context_breakdown.py
git commit -m "feat: compute auto_compact on the context breakdown

The decision lives on the backend so switching it on the VM is a start.sh
restart rather than a rebuild and re-sideload of the add-in, and because
compressible_messages is a store query the client cannot derive.

compaction_auto_min_messages is the one new piece of policy. After a compaction
compressible history drops to ~0 and grows two rows per turn, so sharing the
button's '> 0' threshold would fire an LLM call every turn against a segment
the net-benefit guard then declines. The manual button keeps its threshold."
```

---

### Task 2: Client — `auto_compact` through the gauge

**Files:**
- Modify: `clients/word/src/contextGauge.ts` (the `ContextBreakdown` interface, and `withLiveDocument`)
- Modify: `scripts/check.sh` (`EXPECTED_PASS_COUNT`)
- Test: `clients/word/src/contextGauge.test.ts`

**Interfaces:**
- Consumes: the backend's `auto_compact: bool` field from Task 1.
- Produces: `ContextBreakdown` gains `auto_compact: boolean`. `withLiveDocument(b: ContextBreakdown, docChars: number): ContextBreakdown` returns a breakdown whose `auto_compact` is narrowed by the live document but never widened. Task 3 reads `shown.auto_compact` off this return value.

- [ ] **Step 1: Write the failing assertions**

In `clients/word/src/contextGauge.test.ts`, first add `auto_compact: true` to the `MSA` fixture, immediately after the `can_compact: true,` line:

```ts
  can_compact: true,
  auto_compact: true,
```

Then append these three assertions immediately after the existing `withLiveDocument` assertions (the block ending with the `compressible_messages: 0` case, around line 82):

```ts
// auto_compact is NARROWED by a live document edit and never widened. A shrunk
// document genuinely relieves the pressure, so auto should stop firing.
assert(
  withLiveDocument(MSA, 42000).auto_compact === false,
  "a shrunk document relieves the pressure -> auto stops firing",
);
// The other direction is the one that matters. The backend folded compaction_auto
// and the message floor into this field; the client can see neither, so a live
// document edit must never turn auto ON. Showing a button on the client's own
// estimate is one thing; firing an unrequested LLM call on it is another.
assert(
  withLiveDocument({ ...MSA, auto_compact: false }, 200000).auto_compact === false,
  "a grown document never turns auto on — the client cannot see the flag or the floor",
);
assert(
  withLiveDocument(MSA, 84859).auto_compact === true,
  "an unchanged document leaves auto armed",
);
```

- [ ] **Step 2: Run the assertions to verify they fail**

Run: `cd clients/word && npx tsx src/contextGauge.test.ts`
Expected: FAIL. `npx tsc --noEmit` will also report `TS2353`/`TS2739` on the fixture, because `auto_compact` is not yet on the interface.

- [ ] **Step 3: Add the field and narrow it in the recompute**

In `clients/word/src/contextGauge.ts`, add to the `ContextBreakdown` interface immediately after `can_compact: boolean;`:

```ts
  can_compact: boolean;
  auto_compact: boolean;
```

Then in `withLiveDocument`, replace the returned `can_compact` line:

```ts
    can_compact: b.compressible_messages > 0 && pct >= b.warn_pct,
```

with:

```ts
    can_compact: b.compressible_messages > 0 && pct >= b.warn_pct,
    // Narrowed here, NEVER widened — note this reads b.auto_compact rather than
    // recomputing from scratch the way can_compact does. The backend already folded
    // compaction_auto and compaction_auto_min_messages into that field and the
    // client knows neither, so a live document edit may switch auto OFF (the
    // pressure is genuinely relieved) but must never switch it ON. The asymmetry
    // with can_compact is deliberate: making a button appear on the client's own
    // estimate of a document the backend has not seen is cheap, and firing an
    // unrequested LLM call on the same estimate is not.
    auto_compact: b.auto_compact && pct >= b.warn_pct,
```

Also extend the function's docstring. Replace:

```ts
 * `can_compact` recomputes its threshold half here — editing the document
 * genuinely changes whether the budget is under pressure — but takes
 * `compressible_messages` as backend truth, since the client cannot know what
 * is in the store. The backend reports 0 when compaction is disabled, so the
 * master switch survives this path.
 */
```

with:

```ts
 * `can_compact` recomputes its threshold half here — editing the document
 * genuinely changes whether the budget is under pressure — but takes
 * `compressible_messages` as backend truth, since the client cannot know what
 * is in the store. The backend reports 0 when compaction is disabled, so the
 * master switch survives this path.
 *
 * `auto_compact` is narrowed rather than recomputed: it can go false here but
 * never true. See the comment on it below.
 */
```

- [ ] **Step 4: Run the assertions to verify they pass**

Run: `cd clients/word && npx tsc --noEmit && npx tsx src/contextGauge.test.ts`
Expected: `tsc` clean; the script ends with `contextGauge: all assertions passed`.

- [ ] **Step 5: Bump the assertion count in the gate**

`scripts/check.sh` pins the total `PASS:` count so a test file that exits early cannot silently run fewer assertions. Three were added, so change:

```bash
EXPECTED_PASS_COUNT=282
```

to:

```bash
EXPECTED_PASS_COUNT=285
```

- [ ] **Step 6: Run the full gate**

Run: `bash scripts/check.sh`
Expected: all checks pass, and the assertions line reads `285/285 PASS`. If it reports a different actual count, the count in the file is what is correct — set `EXPECTED_PASS_COUNT` to the reported number and re-run rather than adjusting assertions to reach 285.

- [ ] **Step 7: Commit**

```bash
git add clients/word/src/contextGauge.ts clients/word/src/contextGauge.test.ts scripts/check.sh
git commit -m "feat: carry auto_compact through the context gauge

Narrowed by a live document edit, never widened: the backend folded the flag
and the message floor into the field and the client can see neither, so a
document edit may relieve pressure but must not arm an unrequested LLM call."
```

---

### Task 3: Client — fire it automatically

**Files:**
- Modify: `clients/word/src/components/ContextMeter.tsx`
- Modify: `clients/word/src/App.tsx` (the `<ContextMeter …>` usage, around line 163)

**Interfaces:**
- Consumes: `ContextBreakdown.auto_compact` and `withLiveDocument` from Task 2.
- Produces: `ContextMeter` props change from `{ breakdown: ContextBreakdown | null }` to `{ breakdown: ContextBreakdown | null; liveDocChars: number | null; docTruncated: boolean }`. `App.tsx` is the only call site.

**Why the props change, and why the obvious effect key is wrong.** `App.tsx` currently calls `withLiveDocument(breakdown, liveDocChars)` inline in JSX. That allocates a **new object on every render**, so an effect keyed on the object `ContextMeter` receives would fire on every render — several unrequested LLM calls per turn, racing each other past the `busy` guard. The object that changes exactly once per turn is the **raw** breakdown in `App.tsx` state. So the adjustment moves into the component: the effect keys on the raw `breakdown`, and decides on the adjusted `shown`.

- [ ] **Step 1: Replace `ContextMeter.tsx` in full**

There is no React test harness in this repo, so this step is a single edit verified by `tsc` and by sideload. Replace the whole file with:

```tsx
import { useEffect, useRef, useState } from "react";
import { compactConversation } from "../api";
import { resolveDocumentId } from "../docIdentity";
import {
  NEVER_COMPACTED,
  PART_LABELS,
  gaugeLine,
  isWarning,
  reclaimTarget,
  withLiveDocument,
  type ContextBreakdown,
} from "../contextGauge";

interface Props {
  breakdown: ContextBreakdown | null;
  liveDocChars: number | null;
  docTruncated: boolean;
}

/**
 * The shared-header context counter.
 *
 * Three honesty constraints, all load-bearing:
 *  - the figures are the LAST TURN'S real measured values, labelled as such.
 *    Not a prediction: grounding is question-dependent and unknowable ahead of
 *    the question.
 *  - the manual Condense action appears only when the budget is under pressure
 *    AND there is compressible history. Offering it with nothing to condense
 *    would offer a no-op, and a control that cries wolf gets ignored.
 *  - condensing that happens WITHOUT a click still says so, before and after.
 *    Nothing is deleted either way, but the attorney should never discover
 *    after the fact that their history was summarised.
 *
 * The live-document adjustment lives here rather than in App.tsx's JSX because
 * withLiveDocument allocates a new object on every call. Keying the auto-fire
 * effect on that object would fire it on every render; keying it on the raw
 * per-turn breakdown fires it once per turn, which is the intent.
 */
export default function ContextMeter({ breakdown, liveDocChars, docTruncated }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [busyAuto, setBusyAuto] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Set when an AUTOMATIC run fails, stopping auto for the life of the pane.
  // The manual path can afford to surface an error on every attempt because a
  // human just pressed a button and is owed an answer; an unrequested error on
  // every turn against a down Ollama teaches attorneys to ignore the pane.
  // Deliberately not persisted: a transient blip must not disable the feature
  // permanently, and there is nowhere honest to persist a client-side judgement
  // about a transient condition. Reopening the pane re-arms it.
  const [disarmed, setDisarmed] = useState(false);
  // The raw per-turn breakdown this component has already auto-fired for.
  const firedFor = useRef<ContextBreakdown | null>(null);

  const shown =
    breakdown && liveDocChars !== null && !docTruncated
      ? withLiveDocument(breakdown, liveDocChars)
      : breakdown;

  const runCompaction = async (automatic: boolean) => {
    if (!shown) return;
    setBusy(true);
    setBusyAuto(automatic);
    setError(null);
    setNote(null);
    try {
      const documentId = await resolveDocumentId();
      // How much has to come back for the counter to fall below the warn line. The
      // backend cannot work this out — it has neither the document nor the grounding —
      // but the counter above already measured it.
      const res = await compactConversation(documentId, reclaimTarget(shown));
      if (res.data?.compacted) {
        const n = res.data.messages ?? 0;
        const dropped = res.data.dropped ?? 0;
        // The drop count is stated, never swallowed. Each dropped line failed to
        // match the message it cited, so leaving it out is the safe outcome — but
        // the attorney should know the summary is thinner than the model intended.
        const skipped =
          dropped > 0
            ? ` ${dropped} quote${dropped === 1 ? "" : "s"} couldn't be checked against the message ` +
              `${dropped === 1 ? "it" : "they"} came from and ${dropped === 1 ? "was" : "were"} left out.`
            : "";
        // When the target was missed, say so and name the reason: the attorney is
        // about to see the document truncated again and should know compaction was
        // not the thing that could have prevented it.
        const reclaimed = res.data.reclaimed ?? 0;
        const requested = res.data.requested ?? 0;
        const short =
          requested > 0 && reclaimed < requested
            ? ` Freed ${reclaimed.toLocaleString("en-US")} of the ` +
              `${requested.toLocaleString("en-US")} characters needed — the rest of the ` +
              `context is document, playbook and MSA, which are never condensed.`
            : "";
        const lead = automatic
          ? `Condensed automatically — ${n} earlier messages.`
          : `${n} earlier messages condensed.`;
        setNote(`${lead}${skipped}${short} The counter updates on your next message.`);
      } else {
        setNote(res.data?.reason || "Nothing earlier to condense yet.");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      // compactConversation throws on any non-2xx, and api/routes/compact.py raises
      // 500 for every `error` result — so catching here covers the whole failure
      // surface. A `reason` result is NOT a failure and never reaches this branch.
      if (automatic) setDisarmed(true);
    } finally {
      setBusy(false);
    }
  };

  // Fires at most once per turn: keyed on the RAW breakdown, which App.tsx replaces
  // exactly once per turn, and additionally guarded by a ref so a re-render caused
  // by our own setState cannot re-enter. Must sit above the early return below —
  // hooks cannot run conditionally.
  useEffect(() => {
    if (!breakdown || !shown?.auto_compact) return;
    if (busy || disarmed) return;
    if (firedFor.current === breakdown) return;
    firedFor.current = breakdown;
    void runCompaction(true);
  }, [breakdown, shown?.auto_compact, busy, disarmed]);

  const line = gaugeLine(shown);
  if (!shown || !line) return null;

  return (
    <div className={`context-meter ${isWarning(shown) ? "warn" : ""}`}>
      <button
        className="context-meter-line"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <span>{line}</span>
        <span className="context-meter-caret">{expanded ? "▾" : "▸"}</span>
      </button>
      {expanded && (
        <>
          <p className="context-meter-caption">Measured on your last message.</p>
          <table className="context-meter-table">
            <tbody>
              {shown.parts
                .filter((p) => p.chars > 0)
                .map((p) => (
                  <tr key={p.key}>
                    <td>{PART_LABELS[p.key] ?? p.key}</td>
                    <td className="num">{p.chars.toLocaleString("en-US")}</td>
                    <td className="num">{p.pct}%</td>
                    <td className="context-meter-note">
                      {NEVER_COMPACTED.has(p.key)
                        ? "never condensed"
                        : p.compactable
                          ? "what condensing reclaims"
                          : ""}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </>
      )}
      {busy && busyAuto && (
        <p className="context-meter-note" role="status">
          Condensing earlier turns automatically… (10–30 s)
        </p>
      )}
      {shown.can_compact && !(busy && busyAuto) && (
        <div className="context-meter-actions">
          <button className="secondary" onClick={() => runCompaction(false)} disabled={busy}>
            {busy ? "Condensing… (10–30 s)" : "Condense earlier turns"}
          </button>
          <span className="context-meter-note">
            Condenses your earlier messages into verbatim quotes. Your most recent
            messages stay word-for-word, and nothing is deleted.
          </span>
        </div>
      )}
      {note && <p className="context-meter-note" role="status">{note}</p>}
      {error && <p className="status error">Couldn't condense: {error}</p>}
    </div>
  );
}
```

- [ ] **Step 2: Update the only call site**

In `clients/word/src/App.tsx`, replace:

```tsx
      <ContextMeter
        breakdown={
          breakdown && liveDocChars !== null && !docTruncated
            ? withLiveDocument(breakdown, liveDocChars)
            : breakdown
        }
      />
```

with:

```tsx
      {/* The live-document adjustment moved INTO ContextMeter: withLiveDocument
          allocates a new object per call, and the auto-fire effect has to key on
          the raw per-turn breakdown or it fires on every render. */}
      <ContextMeter
        breakdown={breakdown}
        liveDocChars={liveDocChars}
        docTruncated={docTruncated}
      />
```

- [ ] **Step 3: Drop the now-unused import**

`withLiveDocument` is no longer referenced in `App.tsx`. Change line 13 from:

```tsx
import { withLiveDocument, type ContextBreakdown } from "./contextGauge";
```

to:

```tsx
import { type ContextBreakdown } from "./contextGauge";
```

- [ ] **Step 4: Typecheck**

Run: `cd clients/word && npx tsc --noEmit`
Expected: clean. A `TS6133` on an unused `withLiveDocument` import means Step 3 was skipped.

- [ ] **Step 5: Run the full gate**

Run: `bash scripts/check.sh`
Expected: all checks pass.

- [ ] **Step 6: Commit**

```bash
git add clients/word/src/components/ContextMeter.tsx clients/word/src/App.tsx
git commit -m "feat: fire compaction automatically when the budget is under pressure

The effect keys on the RAW per-turn breakdown, not the object ContextMeter
renders: withLiveDocument allocates a new object on every call, so keying on
its result would fire on every render rather than once per turn. That is why
the live-document adjustment moves into the component.

An automatic failure disarms auto for the life of the pane. The manual path can
surface an error every time because a human pressed a button; an unrequested
error on every turn against a down Ollama trains attorneys to ignore the pane."
```

---

### Task 4: Documentation

**Files:**
- Modify: `CLAUDE.md` (the compaction bullet, currently line 93)
- Modify: `docs/wiki.md` (the Configuration settings list; the "Automatic compaction" follow-up row; a new Shipped row)

**Interfaces:**
- Consumes: everything from Tasks 1–3.
- Produces: nothing code-facing.

**Constraint:** `CLAUDE.md` must stay **at or under 150 lines** — it is currently at 144. Extend the existing compaction bullet in place; do **not** add a new bullet.

- [ ] **Step 1: Extend the CLAUDE.md compaction bullet**

In the `- **History compaction protects the CONTRACT, not the fit.**` bullet, find the sentence:

```
**Every sizing knob is a bound, not a size — the unit must match the thing it protects.**
```

and insert this immediately before it:

```
**Compaction fires automatically** (`compaction_auto`, default True) via `auto_compact` on the breakdown — computed on the BACKEND (`build_context_breakdown`) and merely obeyed by the pane, because the add-in is hand-sideloaded (a client-side flag would mean a rebuild per machine to change it) and because `compressible_messages` is a store query the client cannot derive. `auto_compact` is built FROM `can_compact` so the narrowing is structural, and `withLiveDocument` may narrow it further but **never widens it** — a button appearing on the client's own estimate of a document the backend hasn't seen is cheap; firing an unrequested LLM call on that estimate is not. It carries its OWN floor, `compaction_auto_min_messages=6`, which the manual button does not share: after a compaction, compressible history drops to ~0 and grows exactly two rows per turn, so a shared `>0` threshold would fire a 10–30s call every turn against a segment the net-benefit guard then declines. **The effect keys on the RAW per-turn breakdown, never the object `ContextMeter` renders** — `withLiveDocument` allocates a new object per call, so keying on its result fires on every render; this is why the live-document adjustment lives in `ContextMeter` and not in `App.tsx`'s JSX. An automatic failure **disarms** auto for the pane's life (per-session, not persisted): the manual path can surface an error each time because a human pressed a button, but an unrequested error every turn trains attorneys to ignore the pane.
```

- [ ] **Step 2: Add the settings to the wiki**

In `docs/wiki.md`, in the `Key settings:` list, extend the `COMPACTION_*` bullet. Replace:

```
`COMPACTION_MAX_INJECTED_SEGMENTS` (`3`). All `@lru_cache`'d in `get_settings` → a `start.sh` restart is required.
```

with:

```
`COMPACTION_MAX_INJECTED_SEGMENTS` (`3`), `COMPACTION_AUTO` (`true` — fire without a click), `COMPACTION_AUTO_MIN_MESSAGES` (`6` — the anti-churn floor for firing unasked, which the manual button does not share). All `@lru_cache`'d in `get_settings` → a `start.sh` restart is required. **`COMPACTION_AUTO=false` is the switch to set on a machine that is about to be demoed on**, since automatic firing puts a background 10–30s summarisation on the shared Ollama.
```

- [ ] **Step 3: Retire the follow-up row**

In `docs/wiki.md`, delete the whole `| **Automatic compaction** ("visible but automatic") | Medium | …` row from the Follow-ups table. It has shipped; the Shipped row added in Step 4 replaces it. Leave the "Re-summarising older segments" and "Self-calibrating chars/token" rows alone.

- [ ] **Step 4: Add the Shipped row**

In `docs/wiki.md`, insert immediately above the `| **Compaction gate eval corpus (`gate` kind)** |` row:

```
| **Automatic compaction** | `feat/auto-compaction` | Compaction now fires without a click. The decision is a backend field, `auto_compact` on `report.context_breakdown`, computed as `can_compact AND compaction_auto AND compressible_messages >= compaction_auto_min_messages` — built *from* `can_compact` rather than beside it, so "auto is a narrowing of the button" is structural and cannot drift. Backend rather than client for two reasons: the add-in is hand-sideloaded, so a client flag would mean a rebuild and re-sideload per machine to change the behaviour, and `compressible_messages` is a store query the client cannot derive. **The new floor is the only new policy, and it prevents a churn loop the manual button never had:** after a compaction, compressible history drops to ~0 and grows by exactly two rows per turn, so sharing the button's `> 0` threshold would fire on the very next turn against two short messages — a segment the net-benefit guard then declines, i.e. a 10–30s LLM call on a shared Ollama guaranteed to write nothing. Six is three turns of accumulation. **A design defect caught in spec self-review is worth recording:** the obvious effect key — the breakdown object `ContextMeter` renders — fires on *every render*, because `App.tsx` allocated it inline via `withLiveDocument`. The object that changes once per turn is the raw one in `App.tsx` state, so the live-document adjustment moved into the component; the effect keys on the raw breakdown and decides on the adjusted one. `withLiveDocument` narrows `auto_compact` but never widens it: the backend folded the flag and the floor into that field and the client can see neither, so a document edit may relieve pressure but must not arm an unrequested LLM call — a deliberate asymmetry with `can_compact`, which is recomputed from scratch. An automatic failure disarms auto for the life of the pane (per-session, not persisted — a transient Ollama blip must not kill the feature, and reopening the pane re-arms it); a `reason` result is not a failure and does not disarm. Concurrency was checked rather than assumed: a compaction committing mid-turn can only *duplicate* context, never lose it, because `latest_to_id` is `MAX(to_id)` and the newest segment is by construction inside the injection window — so no locking or send-blocking was introduced. Not in this slice: server-side firing (it would put the measured 10–30s on the attorney's own answer, and `compact_conversation` cannot compute its own target), and the summary viewer, which stays a follow-up. Honest limit: the effect itself has no automated coverage — the add-in has no React test harness — so it was verified by sideload on the VM. Spec: `docs/superpowers/specs/2026-08-26-automatic-compaction-design.md`. |
```

- [ ] **Step 5: Verify the line cap and the gate**

Run: `wc -l CLAUDE.md && bash scripts/check.sh`
Expected: `CLAUDE.md` is at most 150 lines; all checks pass.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md docs/wiki.md
git commit -m "docs: record automatic compaction

Includes the two gotchas worth keeping: the effect must key on the raw per-turn
breakdown because withLiveDocument allocates a new object per call, and
auto_compact may be narrowed by a live document edit but never widened."
```

---

## Manual verification (after Task 4, before merge)

None of this is reachable by `scripts/check.sh`, and the effect has no automated coverage at all. Sideload in Word for Mac (`cd clients/word && npm run dev`, backend via `bash scripts/start.sh`) and confirm:

1. **It fires once, not repeatedly.** Open a large contract, chat until the gauge goes amber, and watch the backend log for `[compaction] condensed rows` — exactly one line per turn that crosses the threshold, never several.
2. **The floor holds.** Immediately after an automatic run, send another message. `auto_compact` must be false (fewer than 6 compressible messages), so nothing fires and no second `[compaction]` line appears. This is the churn loop the floor exists to prevent, and it is the single most important thing to see.
3. **It is visible.** The "Condensing earlier turns automatically… (10–30 s)" line appears while it runs, and the completion note reads `Condensed automatically — N earlier messages.`
4. **Chat stays usable during a run.** Send a message while the automatic run is in flight; the turn answers normally.
5. **Disarm works.** Stop Ollama, force the threshold, confirm the error renders once and does not reappear on the next turn — and that the manual button still works after Ollama is back.
6. **The switch works.** Set `COMPACTION_AUTO=false` in `.env`, restart `bash scripts/start.sh`, and confirm nothing fires automatically while the manual button behaves exactly as before.
