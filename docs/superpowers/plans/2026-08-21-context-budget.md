# Context Budget and Loud Truncation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the agent answering legal questions about contract text it never received — by pinning the context window to what the inference server actually serves, deriving the chat budget from a measured latency ceiling, and making every remaining truncation visible to the attorney.

**Architecture:** Four config changes plus one new plumbing path. The truncation
detail travels: `_cap_chat_context` returns it → `_run_doc_chat` writes it to
state → `output_formatter` maps state into `report` → `query.py` returns the
report wholesale → the pane renders a notice. Token usage is *already*
extracted by `observability/tracing.py`; this plan routes the existing value and
writes no new extraction logic.

**Tech Stack:** Python 3.12, uv, FastAPI, LangGraph, pydantic-settings, ChatOllama; React + TypeScript + Vite for the Word add-in; pytest (needs Docker — `tests/conftest.py` spins an ephemeral Postgres).

**Spec:** [docs/superpowers/specs/2026-08-21-context-budget-design.md](../specs/2026-08-21-context-budget-design.md)

## Global Constraints

- **All imports at top of file.** No lazy imports inside functions.
- **Don't add backwards-compat shims.** Change call sites instead.
- `ollama_num_ctx` must be **EQUAL** to the server's window, never merely large enough — Ollama reloads the model when a request's `num_ctx` differs in *either* direction.
- **`skills/legal_research/` is a package: patch the module whose globals the call path resolves through.** `legal_research.py` re-imports `_cap_chat_context` from `context.py`, so the name lives on **both** modules bound to the same object. Patch `context` when calling it directly; patch `legal_research` when driving it through `legal_research(state)`. The wrong target silently no-ops and the test passes while testing nothing — verify by mutation, not by a green suite.
- **A flag set in a skill (before `output_formatter`) may use `state[...]`. A flag set in `memory_writer` (after `output_formatter`) MUST travel on the returned report.** Everything in this plan is set before `output_formatter`, so state is correct throughout — but any test must assert the value in the **report**, not merely that a state key was set.
- **`tests/conftest.py` truncates a HARDCODED table list.** This plan adds no store tables, so no change is needed there.
- Restart `bash scripts/start.sh` after any `config.py` change — `get_settings` is `@lru_cache`'d.
- Gate: `bash scripts/check.sh`. It pins `EXPECTED_PASS_COUNT` for add-in assertions — **derive the new number by running the suite, never predict it.**
- Exact measured constants, to be used verbatim: window **131072**; chat budget **150000**; chars/token **4.89**; prefill **1,397 tok/s**; generation **51.4 tok/s**; Trinetix MSA **84,859** chars; MSA playbook bundle **38,587** chars; combined **123,446** chars.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `config.py` | modify `:59`, `:73`, add one field | The three budget numbers and their derivation, as comments that are true |
| `tests/test_config.py` | add 2 tests | Invariants that make a future misconfiguration fail loudly |
| `skills/legal_research/context.py` | modify `_cap_chat_context` | Return what was cut instead of only logging it |
| `skills/legal_research/legal_research.py` | modify `_run_doc_chat`, `legal_research` | Write truncation + token usage to state; reset per turn |
| `graph/state.py` | add 2 fields | Declare the two new state keys |
| `graph/nodes/output_formatter.py` | add 2 report keys | Map state → report (the only state→payload mapper) |
| `graph/nodes/llm_caller.py` | route usage, add overflow guard | Review-path token usage + detection of >headroom input |
| `clients/word/src/contextNotice.ts` | **create** | Pure formatter: truncation detail → attorney-readable sentence |
| `clients/word/src/contextNotice.test.ts` | **create** | Assertions for that formatter |
| `clients/word/src/api.ts` | extend `QueryResponse` | Type the two new report fields |
| `clients/word/src/components/ChatTab.tsx` | add notice | Render it on the chat path |
| `clients/word/src/components/FindingsTab.tsx` | add notice | Render it on the review path |
| `clients/word/src/styles.css` | add `.context-truncated` | A visual identity distinct from `.status.warning` |
| `scripts/check.sh` | bump `EXPECTED_PASS_COUNT` | Keep the assertion-count gate honest |
| `CLAUDE.md`, `docs/wiki.md` | document | Record the gotcha and the shipped work |

### Deliberate deviation from the spec

The spec says the banner should follow the `memory_degraded` pattern using
`.status.warning`. **Do not reuse that class.** `styles.css:889` documents an
explicit rule: `.status.warning` already means *"this turn wasn't remembered"*,
and the codebase deliberately gave `.unsaved-notice` its own look because *"if
the two looked alike, the one that fires on almost every pilot document would
train testers to ignore the one that fires rarely."*

Truncation is a **more severe** condition than either: memory loss costs
convenience, truncation means the answer may be legally unsound. It gets its own
class, styled to read as more serious than amber. Same posture as the spec asked
for (non-blocking, never silent), different pixels.

---

## Task 1: Config — pin the window, derive the budget, name the ratio

**Files:**
- Modify: `config.py:59` (`ollama_num_ctx`), `config.py:73` (`chat_context_max_chars`), add `est_chars_per_token`
- Test: `tests/test_config.py`, `tests/test_skills.py` (the anti-drift test in Step 1b)

**Interfaces:**
- Consumes: nothing
- Produces: `settings.ollama_num_ctx == 131072`, `settings.chat_context_max_chars == 150000`, `settings.est_chars_per_token == 4.89` (a `float`). Later tasks read `est_chars_per_token` for the review-path guard.

- [ ] **Step 1: Write the failing invariant tests**

Append to `tests/test_config.py`:

```python
def test_chat_budget_fits_context_window():
    """The assembled chat budget plus the answer must fit inside the pinned window.

    This invariant was violated before 2026-08-21: chat_context_max_chars was
    set without reference to ollama_num_ctx, so a full MSA turn overflowed and
    Ollama silently middle-dropped the prompt — which removes exactly the
    playbook/MSA. Asserting it here makes a future mis-tune fail loudly.
    """
    s = get_settings()
    est_input_tokens = s.chat_context_max_chars / s.est_chars_per_token
    assert est_input_tokens + s.ollama_num_predict_chat < s.ollama_num_ctx, (
        f"chat budget {s.chat_context_max_chars} chars "
        f"(~{est_input_tokens:.0f} tok) + {s.ollama_num_predict_chat} answer tokens "
        f"does not fit num_ctx={s.ollama_num_ctx}"
    )


def test_review_headroom_fits_a_real_contract():
    """contract_review has NO input cap, so the window must hold the largest
    real document we have plus its playbook bundle.

    Measured 2026-08-21: the Trinetix Model MSA extracts to 84,859 chars and
    the MSA playbook bundle assembles to 38,587 — 123,446 together. At the old
    num_ctx=32768 with num_predict_review=8192 the headroom was 24,576 tokens
    against a measured 25,270-token input, so MSA reviews ran under-grounded.
    """
    s = get_settings()
    headroom_chars = (s.ollama_num_ctx - s.ollama_num_predict_review) * s.est_chars_per_token
    assert headroom_chars > 123_446, (
        f"review headroom {headroom_chars:.0f} chars cannot hold a real MSA review "
        f"(123,446 chars): num_ctx={s.ollama_num_ctx}, "
        f"num_predict_review={s.ollama_num_predict_review}"
    )
```

- [ ] **Step 1b: Write the anti-drift test for the three `num_ctx` sites**

Three separate tests already assert each site forwards `ollama_num_ctx`, but each
can pass while the sites have silently diverged onto different constants. Since a
mismatch in *either* direction reloads a 24GB model, they need pinning together.

Append to `tests/test_skills.py`, under the existing `# --- num_ctx wiring` heading:

```python
def test_all_num_ctx_sites_read_the_same_setting(monkeypatch):
    """All three consumers must read settings.ollama_num_ctx — one field, no drift.

    If any site acquired its own constant or its own config field, a request
    could go out with a num_ctx different from the resident model's, and Ollama
    reloads the model on ANY mismatch, in either direction (measured 4.7s for a
    24GB model on Spark). The three existing per-site tests would each still
    pass in that world; this one would not.
    """
    import httpx
    from config import get_settings
    from graph.nodes import llm_caller as caller

    lr = importlib.import_module("skills.legal_research.legal_research")
    sentinel = 54321
    monkeypatch.setenv("OLLAMA_NUM_CTX", str(sentinel))
    get_settings.cache_clear()
    lr._llm_cache.clear()
    try:
        assert getattr(lr._build_llm(), "num_ctx", None) == sentinel
        assert getattr(lr._build_json_llm(), "num_ctx", None) == sentinel

        captured: dict = {}

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"message": {"content": "ok"}}

        def _fake_post(url, json=None, timeout=None):
            captured.update(json or {})
            return _Resp()

        monkeypatch.setattr(httpx, "post", _fake_post)
        caller.llm_caller(
            {"request": "q", "task_type": "contract_review", "retrieved_chunks": []}
        )
        assert captured["options"]["num_ctx"] == sentinel
    finally:
        lr._llm_cache.clear()
        get_settings.cache_clear()
```

Note the established conventions this follows: `importlib.import_module` (never
`import skills.legal_research.legal_research as X` — `__init__.py` re-exports the
function over the submodule), and the `get_settings.cache_clear()` +
`lr._llm_cache.clear()` pairing in a `finally` so a failure cannot leak a
poisoned cache into later tests.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py::test_chat_budget_fits_context_window tests/test_config.py::test_review_headroom_fits_a_real_contract -v`

Expected: both FAIL. The first with `AttributeError: 'Settings' object has no attribute 'est_chars_per_token'`. (Confirm the second fails for the same reason, then again on the assertion once the field exists — see Step 4.)

Also run the anti-drift test: `uv run pytest tests/test_skills.py -k all_num_ctx_sites -v`

Expected: **PASS immediately.** This one is deliberately not a failing-first test —
the three sites already read one field, and it exists to stop them drifting apart
later. Prove it is load-bearing by mutation instead: temporarily hardcode
`num_ctx=4096` in `_build_json_llm`, confirm the test FAILS, then restore. A guard
that cannot fail is not a guard.

- [ ] **Step 3: Make the config changes**

Replace `config.py:59` — this exact line:

```python
    ollama_num_ctx: int = 32768            # context window for grounded LLM calls (playbook+MSA+doc+answer); qwen3.6 supports 262k. Raise/lower per hardware (bigger = more KV-cache RAM).
```

with:

```python
    # Pinned to the window the inference server actually loads. This must be
    # EQUAL to the server's, not merely large enough: Ollama reloads the model
    # whenever a request's num_ctx differs from the resident one, in EITHER
    # direction. Measured 2026-08-21 on Spark — requesting 32768 against a
    # resident 131072 forced a 4.7s reload and dropped it to 27.07GB — so a
    # "conservatively smaller" value is not safe, it thrashes a 24GB model in
    # and out and contends with every other consumer of that box.
    #
    # 131072 is what Spark (172.20.0.22) serves. KV costs only ~49.5 MB per 1k
    # tokens because qwen3.6 is a hybrid SSM/attention MoE
    # (full_attention_interval=4 over block_count=40 => ~10 attention layers;
    # the other 30 are SSM layers holding constant-size state), so 131072 costs
    # ~6.34GB and even the full 262144 window costs ~12.7GB. Memory is not the
    # constraint here; prefill latency is (see chat_context_max_chars).
    #
    # COUPLING: this tracks the server's OLLAMA_CONTEXT_LENGTH. If the service
    # is retuned we get reload thrash — a visible 4.7s penalty, not silent
    # truncation, which is the right failure mode. Verify after deploy:
    #   curl http://<ollama-host>:11434/api/ps   -> ctx must equal this value.
    ollama_num_ctx: int = 131072
```

Replace `config.py:73` — this exact line:

```python
    chat_context_max_chars: int = 100000   # assembled chat-context budget; must stay below ollama_num_ctx (in tokens ≈ chars/4) with answer headroom — at 32768 tokens that is ~100k chars plus ~7k tokens answer room.
```

with:

```python
    # Derived from a 30s turn ceiling, not chosen. Measured on Spark 2026-08-21
    # (qwen3.6: prefill 1,397 tok/s, generation 51.4 tok/s):
    #     answer   400 tok / 51.4 tok/s        =   7.8s
    #     prefill  (30 - 7.8) * 1,397 tok/s    =  31,013 tokens
    #     chars    31,013 * 4.89               = ~151,653  -> 150,000
    #
    # At 150k the full Trinetix MSA (84,859) + MSA playbook bundle (38,587) +
    # a prior-review block (~5,000) all fit, leaving ~21,500 for history.
    #
    # NOTE the constraint has moved: 150k chars is ~30.7k tokens against a
    # 131,072 window — 4x headroom. The WINDOW is no longer binding, PREFILL
    # LATENCY is. That is why this is not simply set to the window, and why
    # history compaction is about bounding prefill rather than about fitting.
    chat_context_max_chars: int = 150000
```

Add immediately after the `chat_context_max_chars` block:

```python
    # Measured on real legal text, not a rule of thumb: the Trinetix MSA plus
    # its playbook bundle is 123,612 chars = 25,270 real prompt tokens. The
    # familiar chars/4 estimate overstates token counts by ~22%, which is why
    # every budget comment that used it was wrong. Used for the budget
    # invariants in tests/test_config.py and the review-path overflow guard in
    # graph/nodes/llm_caller.py.
    est_chars_per_token: float = 4.89
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`

Expected: all PASS. Sanity-check the arithmetic by hand — `150000/4.89 + 2048 = 32,720 < 131,072` and `(131072-8192)*4.89 = 600,883 > 123,446`.

- [ ] **Step 5: Run the whole backend suite**

Run: `uv run pytest tests/ -q`

Expected: all pass. No test pins the old `32768` default (verified — the two mentions at `tests/test_observability.py:321` and `tests/test_skills.py:1431` are docstrings, corrected in Task 6). If anything fails, it is a genuine coupling to the old value — report it rather than adjusting the new constants.

- [ ] **Step 6: Commit**

```bash
git add config.py tests/test_config.py tests/test_skills.py
git commit -m "fix: pin num_ctx to the server window and derive the chat budget

ollama_num_ctx 32768 -> 131072. Not a tuning change: Spark already serves
131072, and Ollama reloads the model whenever a request's num_ctx differs in
either direction, so asking for 32768 forced a 4.7s reload of a 24GB model
AND capped review input at 24,576 tokens against a measured 25,270-token MSA
review — which Ollama then middle-dropped, removing exactly the playbook/MSA.

chat_context_max_chars 100000 -> 150000, derived from the agreed 30s ceiling
at a measured 1,397 tok/s prefill and 51.4 tok/s generation.

New est_chars_per_token=4.89, measured (123,612 chars = 25,270 real tokens).
The chars/4 rule of thumb every budget comment used overstates by ~22%.

Two invariant tests encode the failure that shipped: the chat budget plus its
answer must fit the window, and review headroom must hold a real MSA."
```

---

## Task 2: `_cap_chat_context` reports what it cut

**Files:**
- Modify: `skills/legal_research/context.py:163-181` (`_cap_chat_context`)
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: `settings.chat_context_max_chars` from Task 1.
- Produces: `_cap_chat_context(messages, uploaded_text, request) -> dict | None`. Returns `None` when nothing was cut; otherwise `{"doc_chars": int, "kept_chars": int, "kept_pct": int}` where `kept_pct` is an `int` percentage floored via integer division. Still mutates `messages` in place. Task 3 consumes the return value.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_skills.py`:

```python
# --- _cap_chat_context truncation reporting ---


def test_cap_chat_context_returns_none_when_within_budget(monkeypatch):
    """Nothing cut -> None, so callers can distinguish 'fine' from 'truncated'.

    Must be None rather than a zero-truncation dict: the pane renders on
    truthiness, and a dict would fire the notice on every healthy turn.
    """
    from skills.legal_research import context as ctx

    monkeypatch.setattr(ctx, "get_settings", lambda: SimpleNamespace(chat_context_max_chars=10_000))
    doc = "x" * 100
    messages = [{"role": "user", "content": doc}]
    assert ctx._cap_chat_context(messages, doc, "q") is None


def test_cap_chat_context_reports_what_it_cut(monkeypatch):
    """Overflow -> the real numbers, so the attorney can be told how much is missing."""
    from skills.legal_research import context as ctx

    monkeypatch.setattr(ctx, "get_settings", lambda: SimpleNamespace(chat_context_max_chars=1_000))
    doc = "x" * 5_000
    messages = [{"role": "system", "content": "y" * 500}, {"role": "user", "content": doc}]
    result = ctx._cap_chat_context(messages, doc, "q")

    assert result is not None
    assert result["doc_chars"] == 5_000
    assert 0 < result["kept_chars"] < 5_000
    assert result["kept_pct"] == result["kept_chars"] * 100 // 5_000
    # The document really was shortened in place, not merely reported on.
    assert len(messages[-1]["content"]) < 5_000 + 200


def test_cap_chat_context_reports_zero_pct_when_document_fully_dropped(monkeypatch):
    """max(0, ...) can reduce the document to nothing; that must report 0%, not crash.

    This is the worst case and the one most worth naming: the turn proceeds and
    answers legal questions having seen none of the contract.
    """
    from skills.legal_research import context as ctx

    monkeypatch.setattr(ctx, "get_settings", lambda: SimpleNamespace(chat_context_max_chars=10))
    doc = "x" * 5_000
    messages = [{"role": "system", "content": "y" * 9_000}, {"role": "user", "content": doc}]
    result = ctx._cap_chat_context(messages, doc, "q")

    assert result is not None
    assert result["kept_chars"] == 0
    assert result["kept_pct"] == 0
```

If `SimpleNamespace` is not already imported in `tests/test_skills.py`, add `from types import SimpleNamespace` to the imports at the top of the file (all imports at top — never inside a function).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_skills.py -k cap_chat_context -v`

Expected: FAIL — the first with `assert None is None` passing but the others failing on `result is not None`, because the function currently returns `None` unconditionally.

- [ ] **Step 3: Implement the return value**

Replace `_cap_chat_context` in `skills/legal_research/context.py` entirely:

```python
def _cap_chat_context(messages: list[dict], uploaded_text: str, request: str) -> dict | None:
    """If total assembled content exceeds the budget, truncate ONLY the document
    portion of the trailing user message — never the grounding. Mutates messages
    in place.

    Returns None when nothing was cut, else what was lost:
        {"doc_chars": int, "kept_chars": int, "kept_pct": int}

    The return value exists because a log line is invisible to the attorney. A
    truncated contract means the answer may be legally unsound — measured
    2026-08-21, a real MSA turn kept only 58% of the document at the old budget
    and 23% with a full history window, and the cut is a TAIL cut, so what goes
    missing is liability, indemnity, term/termination, governing law and the
    signature blocks. The caller routes this to the report so the pane can say
    so. See docs/superpowers/specs/2026-08-21-context-budget-design.md.
    """
    budget = get_settings().chat_context_max_chars
    total = sum(len(m["content"]) for m in messages)
    if total <= budget:
        return None
    overflow = total - budget
    keep = max(0, len(uploaded_text) - overflow - len("\n\n[document truncated for context budget]"))
    truncated_doc = uploaded_text[:keep] + "\n\n[document truncated for context budget]"
    messages[-1]["content"] = (
        f"--- ATTACHED DOCUMENT (the source of truth — answer from this) ---\n"
        f"{truncated_doc}\n"
        f"--- END ATTACHED DOCUMENT ---\n\n"
        f"User request: {request}"
    )
    logger.warning("[legal_research] chat context %d > budget %d — truncated document to %d chars",
                   total, budget, keep)
    doc_chars = len(uploaded_text)
    return {
        "doc_chars": doc_chars,
        "kept_chars": keep,
        "kept_pct": (keep * 100 // doc_chars) if doc_chars else 0,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_skills.py -k cap_chat_context -v`

Expected: 3 PASS.

- [ ] **Step 5: Prove the tests are not vacuous**

Temporarily change the `return {...}` block to `return None`, re-run the three tests, and confirm two of them FAIL. Then restore. A test that passes with the mechanism deleted is testing nothing — this branch's predecessor shipped five defects of exactly that shape.

- [ ] **Step 6: Commit**

```bash
git add skills/legal_research/context.py tests/test_skills.py
git commit -m "feat: _cap_chat_context returns what it truncated

Returns None when nothing was cut, else {doc_chars, kept_chars, kept_pct}.
Previously the only record was a logger.warning, invisible to the attorney,
while the turn answered legal questions about a contract it had seen 58% of
(23% with a full history window). Tail cut, so the missing part is liability,
indemnity, term/termination, governing law and the signature blocks."
```

---

## Task 3: Route chat truncation and token usage to the payload

**Files:**
- Modify: `graph/state.py` (2 new fields), `skills/legal_research/legal_research.py` (`_run_doc_chat`, `legal_research`), `graph/nodes/output_formatter.py:15-31`
- Test: `tests/test_nodes.py`, `tests/test_skills.py`, `tests/test_api.py` (the boundary guard in Step 8b)

**Interfaces:**
- Consumes: `_cap_chat_context(...) -> dict | None` from Task 2; `message_usage(message) -> TokenUsage | None` from `observability/tracing.py` (already exists — do not rewrite it).
- Produces: `state["context_truncated"]` and `state["token_usage"]`, mapped by `output_formatter` to `report["context_truncated"]` and `report["tokens"]`. `api/routes/query.py:126,132` returns the report wholesale, so no API change is needed. Task 5 consumes the two report keys.

> **Naming asymmetry — intentional, do not "tidy" it.** The state key is
> `token_usage`; the report key is `tokens`. State names the thing
> (`observability.tracing.TokenUsage`), the payload uses the short public name
> the client reads. Renaming either half breaks the other, and `output_formatter`
> is the only place the two meet. `context_truncated` keeps the same name on both
> sides.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_nodes.py`:

```python
def test_output_formatter_maps_context_truncated_and_tokens():
    """The two new state fields must reach the report — that is the only path
    to the pane, since query.py returns report wholesale."""
    from graph.nodes.output_formatter import output_formatter

    truncation = {"doc_chars": 84859, "kept_chars": 49537, "kept_pct": 58}
    usage = {"input": 25270, "output": 412, "total": 25682, "unit": "TOKENS"}
    state = {
        "task_type": "research",
        "llm_response": "answer",
        "context_truncated": truncation,
        "token_usage": usage,
    }
    result = output_formatter(state)
    assert result["report"]["context_truncated"] == truncation
    assert result["report"]["tokens"] == usage


def test_output_formatter_defaults_new_fields_to_none():
    """A healthy turn reports null, not a zero-filled object.

    output_formatter names every key explicitly, so these are always PRESENT in
    the payload; the client tests truthiness. 'Absent' is not achievable with
    this pattern and must not be specified.
    """
    from graph.nodes.output_formatter import output_formatter

    result = output_formatter({"task_type": "research", "llm_response": "answer"})
    assert result["report"]["context_truncated"] is None
    assert result["report"]["tokens"] is None
```

Append to `tests/test_skills.py`:

```python
def test_legal_research_resets_context_truncated_each_turn(monkeypatch):
    """A prior turn's truncation flag must not leak into a clean turn.

    Same reasoning as the existing proposed_edits reset: the pane would show a
    stale 'I could only read 58%' notice on a turn where the whole document fit.
    """
    from skills.legal_research import legal_research as lr

    monkeypatch.setattr(lr, "_extract_uploaded_text", lambda state: "")
    monkeypatch.setattr(lr, "_run_kb_research", lambda state: ("answer", [], set()))
    state = {
        "request": "q",
        "context_truncated": {"doc_chars": 1, "kept_chars": 0, "kept_pct": 0},
        "token_usage": {"input": 1, "output": 1, "total": 2, "unit": "TOKENS"},
    }
    result = lr.legal_research(state)
    assert result["context_truncated"] is None
    assert result["token_usage"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_nodes.py -k output_formatter_maps tests/test_nodes.py -k defaults_new_fields tests/test_skills.py -k resets_context_truncated -v`

Expected: FAIL — `KeyError: 'context_truncated'` from the report, and the reset test failing because the key is never cleared.

- [ ] **Step 3: Declare the state fields**

In `graph/state.py`, add to the end of the `LegalAgentState` TypedDict, following the existing `# NEW —` comment convention:

```python
    context_truncated: dict | None         # NEW — set when the document was cut to fit the budget; None on a healthy turn
    token_usage: dict | None               # NEW — real prompt/completion counts from the LLM response (observability.tracing shape)
```

- [ ] **Step 4: Map them in output_formatter**

In `graph/nodes/output_formatter.py`, inside the `state["report"] = {...}` dict, add two entries immediately after the existing `"memory_degraded"` line:

```python
        "context_truncated": state.get("context_truncated"),
        "tokens": state.get("token_usage"),
```

Note the deliberate asymmetry: no default argument to `state.get`, so both are `None` rather than a falsy dict.

- [ ] **Step 5: Capture the values in the chat path**

In `skills/legal_research/legal_research.py`, `_run_doc_chat`, replace this line:

```python
    _cap_chat_context(messages, uploaded_text, request)
```

with:

```python
    # Truncation is set on state, not returned, because _run_doc_chat already
    # receives state and _load_prior_review_block sets memory_degraded the same
    # way. Set here, INSIDE the skill, it runs before output_formatter and so
    # reaches the report; a flag set in memory_writer would not (it runs after)
    # — the hazard recorded in CLAUDE.md.
    state["context_truncated"] = _cap_chat_context(messages, uploaded_text, request)
```

Then, immediately after the existing line that extracts `content` from the response:

```python
    content = response.content if hasattr(response, "content") else str(response)
```

add:

```python
    # observability/tracing.py already extracts this for OTel spans; route the
    # same value to state so the pane can show real token counts. Do not
    # re-implement the extraction.
    state["token_usage"] = message_usage(response)
```

Add `message_usage` to the existing import from `observability.tracing` at the top of the file, so it reads:

```python
from observability.tracing import message_usage, traced_agent_invoke, traced_invoke
```

- [ ] **Step 6: Reset both fields per turn**

In `legal_research()`, extend the existing reset block. Replace:

```python
    # Always reset proposed_edits at the start so a turn that produces no
    # edit block doesn't carry the prior turn's proposal forward.
    state["proposed_edits"] = []
    state["proposed_preferences"] = []
```

with:

```python
    # Always reset per-turn outputs at the start so a turn that produces none
    # doesn't carry the prior turn's values forward. For context_truncated this
    # matters visibly: a stale flag would show "I could only read 58% of this
    # document" on a turn where the whole document fit.
    state["proposed_edits"] = []
    state["proposed_preferences"] = []
    state["context_truncated"] = None
    state["token_usage"] = None
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_nodes.py tests/test_skills.py -q`

Expected: all pass.

- [ ] **Step 8: Prove the mapping is load-bearing**

Delete the two lines added to `output_formatter` in Step 4, re-run
`uv run pytest tests/test_nodes.py -k output_formatter -v`, and confirm both new
tests FAIL. Restore them. Then run the full suite:

Run: `uv run pytest tests/ -q`

- [ ] **Step 8b: Lock the payload path at the HTTP layer**

The `output_formatter` tests prove state → report. They do **not** prove report →
JSON: `query.py:126,132` returns the report wholesale today, but a future filter
or key whitelist there would silently drop the truncation notice while every
node-level test kept passing. That is precisely the hazard class this branch
exists to close, so it gets an assertion at the boundary.

Append to `tests/test_api.py`:

```python
def test_query_returns_context_truncated_and_tokens_in_payload(monkeypatch):
    """Report keys must survive to the client, not just into the report dict.

    query.py returns the report wholesale; this locks that in. Note this test
    mocks the graph, so it deliberately does NOT exercise output_formatter —
    tests/test_nodes.py covers that half. Together they cover the whole path.
    """
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("LLM_MODEL", "qwen3.6:latest")
    get_settings.cache_clear()

    truncation = {"doc_chars": 84859, "kept_chars": 49537, "kept_pct": 58}
    usage = {"input": 25270, "output": 412, "total": 25682, "unit": "TOKENS"}

    def _invoke(state, config=None):
        state = _mock_graph_invoke(state, config)
        state["report"]["context_truncated"] = truncation
        state["report"]["tokens"] = usage
        return state

    with patch("api.routes.query._get_graph") as mock_get_graph:
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = _invoke
        mock_get_graph.return_value = mock_graph

        from api.main import app
        client = TestClient(app)
        response = client.post("/api/query", json={"request": "q"})

    assert response.status_code == 200
    report = response.json()["data"]["report"]
    assert report["context_truncated"] == truncation
    assert report["tokens"] == usage
```

Run: `uv run pytest tests/test_api.py -k context_truncated_and_tokens -v`

Expected: **PASS immediately** — a guard, not a failing-first test. Prove it bites
by mutation: in `api/routes/query.py`, temporarily replace `"report": report,` with
`"report": {k: v for k, v in report.items() if k != "context_truncated"},`, confirm
the test FAILS, then restore.

- [ ] **Step 9: Commit**

```bash
git add graph/state.py graph/nodes/output_formatter.py skills/legal_research/legal_research.py tests/test_nodes.py tests/test_skills.py tests/test_api.py
git commit -m "feat: route chat truncation and token usage to the report

_cap_chat_context's return value and the token usage that
observability/tracing.py already extracts now travel: state -> output_formatter
-> report -> (query.py returns report wholesale) -> pane.

Both are set inside the skill, before output_formatter, so state is the correct
carrier here; a flag set in memory_writer would run after output_formatter and
silently never reach the banner. Both reset per turn so a stale 'I could only
read 58%' cannot appear on a turn where the document fit.

Reported as null rather than a zero-filled object, because output_formatter
names every key explicitly and the client renders on truthiness."
```

---

## Task 4: Review path — token usage and overflow detection

**Files:**
- Modify: `graph/nodes/llm_caller.py`
- Test: `tests/test_observability.py`

**Interfaces:**
- Consumes: `settings.est_chars_per_token` from Task 1; `ollama_usage(data)` already called in `llm_caller` for spans; `state["context_truncated"]` shape from Task 2.
- Produces: `state["token_usage"]` on the review path, and `state["context_truncated"]` when assembled review input exceeds the derived headroom. Detection only — no truncation logic.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_observability.py`:

```python
def test_llm_caller_routes_token_usage_to_state(monkeypatch):
    """The usage llm_caller already computes for spans must also reach state,
    so the review path can report real token counts to the pane."""
    import httpx
    from graph.nodes import llm_caller as mod

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "review"}, "prompt_eval_count": 25270,
                    "eval_count": 412}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    state = {"request": "review", "task_type": "contract_review", "retrieved_chunks": []}
    result = mod.llm_caller(state)
    assert result["token_usage"]["input"] == 25270
    assert result["token_usage"]["output"] == 412


def test_llm_caller_flags_review_input_over_headroom(monkeypatch):
    """A review whose assembled input exceeds the window's headroom must SAY so.

    contract_review has no input cap, so at some document size Ollama
    middle-drops the prompt — removing exactly the playbook/MSA. Detection turns
    a silently-wrong review into a visibly-degraded one. It must never block the
    review.
    """
    import httpx
    from graph.nodes import llm_caller as mod

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "review"}}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    # num_ctx 1000 - num_predict_review 500 = 500 tokens * 4.0 chars = 2000 chars headroom
    monkeypatch.setattr(mod, "get_settings", lambda: SimpleNamespace(
        llm_model="m", ollama_base_url="http://x", ollama_num_ctx=1000,
        ollama_num_predict_chat=100, ollama_num_predict_review=500,
        est_chars_per_token=4.0,
    ))
    state = {"request": "x" * 5000, "task_type": "contract_review", "retrieved_chunks": []}
    result = mod.llm_caller(state)

    assert result["llm_response"] == "review"          # never blocked
    assert result["context_truncated"] is not None
    assert result["context_truncated"]["kept_pct"] < 100


def test_llm_caller_does_not_flag_review_within_headroom(monkeypatch):
    """A review that fits leaves the flag alone, so the notice cannot cry wolf."""
    import httpx
    from graph.nodes import llm_caller as mod

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "review"}}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    monkeypatch.setattr(mod, "get_settings", lambda: SimpleNamespace(
        llm_model="m", ollama_base_url="http://x", ollama_num_ctx=131072,
        ollama_num_predict_chat=2048, ollama_num_predict_review=8192,
        est_chars_per_token=4.89,
    ))
    state = {"request": "short request", "task_type": "contract_review", "retrieved_chunks": []}
    result = mod.llm_caller(state)
    assert result.get("context_truncated") is None
```

If `SimpleNamespace` is not already imported at the top of `tests/test_observability.py`, add `from types import SimpleNamespace`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_observability.py -k "routes_token_usage or headroom" -v`

Expected: FAIL — `KeyError: 'token_usage'` and `KeyError: 'context_truncated'`.

- [ ] **Step 3: Implement the guard and the usage routing**

In `graph/nodes/llm_caller.py`, immediately after the existing `chars = sum(...)` line and before the `logger.info("[llm_caller] -> ollama ...")` call, insert:

```python
    # contract_review has no input cap, so past some document size Ollama
    # middle-drops the prompt — which removes exactly the playbook/MSA and
    # yields a confidently under-grounded review with no log line at all.
    # DETECTION ONLY, on purpose: choosing what to sacrifice in a review is a
    # real design question, and the chat path's answer (cut the document) is
    # precisely the bug this change exists to fix. Report it and let the review
    # proceed; a visible degraded answer beats a silent wrong one.
    headroom_chars = int(
        (settings.ollama_num_ctx - settings.ollama_num_predict_review)
        * settings.est_chars_per_token
    )
    if chars > headroom_chars:
        logger.error(
            "[llm_caller] review input %d chars EXCEEDS headroom %d "
            "(num_ctx=%d - num_predict_review=%d at %.2f chars/token) — "
            "the model will silently drop part of this prompt",
            chars, headroom_chars, settings.ollama_num_ctx,
            settings.ollama_num_predict_review, settings.est_chars_per_token,
        )
        state["context_truncated"] = {
            "doc_chars": chars,
            "kept_chars": headroom_chars,
            "kept_pct": headroom_chars * 100 // chars,
        }
```

Then, immediately after the existing `state["llm_response"] = content` line inside the `try` block, add:

```python
        # Same value already handed to set_gen_attributes below; routed to state
        # so the pane can show real token counts. Not a second extraction.
        state["token_usage"] = ollama_usage(data)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_observability.py -v`

Expected: all pass.

- [ ] **Step 5: Prove the guard is load-bearing**

Change `if chars > headroom_chars:` to `if False:`, re-run
`uv run pytest tests/test_observability.py -k headroom -v`, and confirm
`test_llm_caller_flags_review_input_over_headroom` FAILS while
`test_llm_caller_does_not_flag_review_within_headroom` still passes. Restore.

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest tests/ -q`

- [ ] **Step 7: Commit**

```bash
git add graph/nodes/llm_caller.py tests/test_observability.py
git commit -m "feat: review-path token usage + overflow detection

Routes the usage llm_caller already computes for spans onto state, and flags
when assembled review input exceeds the window's headroom.

Detection only. contract_review has no input cap, so past ~600k chars Ollama
middle-drops the prompt and removes exactly the playbook/MSA. Choosing what to
sacrifice in a review is a design question, and the chat path's answer -- cut
the document -- is the very bug this branch fixes, so copying it would be
wrong. The guard reports and never blocks a review."
```

---

## Task 5: Word add-in — notice, types, and its own visual identity

**Files:**
- Create: `clients/word/src/contextNotice.ts`, `clients/word/src/contextNotice.test.ts`
- Modify: `clients/word/src/api.ts`, `clients/word/src/components/ChatTab.tsx`, `clients/word/src/components/FindingsTab.tsx`, `clients/word/src/styles.css`, `scripts/check.sh`

**Interfaces:**
- Consumes: `report.context_truncated` (`{doc_chars, kept_chars, kept_pct} | null`) and `report.tokens` (`{input, output, total, unit} | null`) from Tasks 3 and 4.
- Produces: `truncationNotice(ct: ContextTruncated | null | undefined): string | null` — the attorney-readable sentence, or `null` when there is nothing to say.

- [ ] **Step 1: Write the failing test**

Create `clients/word/src/contextNotice.test.ts`:

```ts
// Assertions for the truncation notice formatter.
// Run: npx tsx src/contextNotice.test.ts
import { truncationNotice } from "./contextNotice";

let passed = 0;
function assert(cond: boolean, name: string) {
  if (!cond) {
    console.error(`FAIL: ${name}`);
    process.exit(1);
  }
  console.log(`PASS: ${name}`);
  passed++;
}

// A healthy turn must produce NOTHING — the notice cannot cry wolf, or testers
// learn to ignore the one condition that means the answer may be unsound.
assert(truncationNotice(null) === null, "null -> no notice");
assert(truncationNotice(undefined) === null, "undefined -> no notice");

const notice = truncationNotice({ doc_chars: 84859, kept_chars: 49537, kept_pct: 58 });
assert(notice !== null, "truncation -> a notice");
assert(notice!.includes("58%"), "notice states the percentage seen");
assert(notice!.includes("35,322"), "notice states the missing char count, thousands-separated");

// The worst case: none of the document survived.
const zero = truncationNotice({ doc_chars: 5000, kept_chars: 0, kept_pct: 0 });
assert(zero !== null, "0% -> a notice");
assert(zero!.includes("none of this document"), "0% is described plainly, not as '0%'");

console.log(`\n${passed} assertions passed`);
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd clients/word && npx tsx src/contextNotice.test.ts`

Expected: FAIL — cannot resolve `./contextNotice`.

- [ ] **Step 3: Implement the formatter**

Create `clients/word/src/contextNotice.ts`:

```ts
// The attorney-facing wording for a truncated context.
//
// Kept as a pure function, separate from JSX, so it can be asserted on. The
// backend sends {doc_chars, kept_chars, kept_pct}; only this module decides how
// to say it.
//
// Why this exists: before 2026-08-21 a truncated document produced only a
// logger.warning in the backend. The attorney asked "is the liability cap
// acceptable?" and got a confident answer from a model that had never seen the
// cap — the document is cut from the TAIL, so liability, indemnity,
// term/termination, governing law and the signature blocks are what go missing.

export interface ContextTruncated {
  doc_chars: number;
  kept_chars: number;
  kept_pct: number;
}

export interface TokenUsage {
  input: number | null;
  output: number | null;
  total: number | null;
  unit: string;
}

/** The sentence to show, or null when the whole document was sent. */
export function truncationNotice(ct: ContextTruncated | null | undefined): string | null {
  if (!ct) return null;
  const missing = Math.max(0, ct.doc_chars - ct.kept_chars);
  // Locale pinned deliberately. Bare toLocaleString() takes the host's ICU
  // locale, so the same code yields "35,322" here and "35 322" or "35.322" on a
  // differently-configured box — which would break the assertion below for a
  // reason that has nothing to do with this code.
  const missingText = missing.toLocaleString("en-US");
  const tail =
    " Clauses near the end of the contract — liability, indemnity, termination," +
    " governing law, signature blocks — may be missing from this answer.";
  if (ct.kept_pct <= 0) {
    return `I could read none of this document (${missingText} characters were not sent).${tail}`;
  }
  return (
    `I could only read ${ct.kept_pct}% of this document ` +
    `(${missingText} characters were not sent).${tail}`
  );
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `cd clients/word && npx tsx src/contextNotice.test.ts`

Expected: all PASS.

- [ ] **Step 5: Type the new report fields**

In `clients/word/src/api.ts`, add the import at the top alongside the existing imports:

```ts
import type { ContextTruncated, TokenUsage } from "./contextNotice";
```

and add two fields inside the `report?: { ... }` block of `QueryResponse`, after `review_persist_error?: string;`:

```ts
      context_truncated?: ContextTruncated | null;
      tokens?: TokenUsage | null;
```

- [ ] **Step 6: Add the distinct style**

In `clients/word/src/styles.css`, append:

```css
/* Truncated-context notice. Deliberately NOT .status.warning: that amber box
   already means "this turn wasn't remembered", and styles.css documents the
   rule that two different meanings must not look alike, or the one that fires
   often trains testers to ignore the one that fires rarely.
   This is the most severe of the three — memory loss costs convenience, an
   unsaved document costs future recall, but a truncated contract means THIS
   ANSWER may be legally unsound — so it reads as more serious than amber. */
.context-truncated {
  color: var(--red-fg);
  background: #fdf3f4;
  border: 1px solid var(--red-fg);
  border-left-width: 3px;
  border-radius: 4px;
  padding: 8px 10px;
  margin: 6px 0;
  font-size: 12px;
}
```

- [ ] **Step 7: Render it on the chat path**

In `clients/word/src/components/ChatTab.tsx`:

Add to the imports:

```ts
import { truncationNotice } from "../contextNotice";
```

Add a state hook beside the existing `memoryDegraded` one:

```ts
  const [truncated, setTruncated] = useState<string | null>(null);
```

In `send()`, beside the existing `setMemoryDegraded(...)` call, add:

```ts
      setTruncated(truncationNotice(res.data?.report?.context_truncated));
```

and in the `res.status === "error"` early-return branch, beside `setMemoryDegraded(false)`, add:

```ts
        setTruncated(null);
```

Render it immediately **before** the existing `{memoryDegraded && (` block, so the more severe notice appears first:

```tsx
      {truncated && (
        <div className="context-truncated" role="alert">
          ⚠ <strong>Part of this document was not sent</strong> — {truncated}
        </div>
      )}
```

- [ ] **Step 8: Render it on the review path**

In `clients/word/src/components/FindingsTab.tsx`:

Add to the imports:

```ts
import { truncationNotice } from "../contextNotice";
```

Add a state hook beside the existing `persistError` one:

```ts
  const [truncated, setTruncated] = useState<string | null>(null);
```

In `onReview()`, beside the existing `const rpe = ...` / `if (rpe) setPersistError(rpe);` lines, add:

```ts
      setTruncated(truncationNotice(res.data?.report?.context_truncated));
```

Render it immediately before the existing `{persistError && (` block:

```tsx
      {truncated && (
        <div className="context-truncated" role="alert">
          ⚠ <strong>Part of this document was not sent</strong> — {truncated} Treat this review as incomplete.
        </div>
      )}
```

- [ ] **Step 9: Typecheck**

Run: `cd clients/word && npx tsc --noEmit`

Expected: clean. If `.js` files appear anywhere under `src/`, delete them — tsconfig is `noEmit:true` and a stray compiled file silently shadows the source.

- [ ] **Step 10: Derive and update the assertion count**

Run the add-in suite exactly as the gate does and read the real total:

```bash
cd clients/word
for f in src/*.test.ts; do npx tsx "$f"; done | grep -c '^PASS: '
```

Take that number and set `EXPECTED_PASS_COUNT` in `scripts/check.sh:26` to it. **Do not predict this value** — a previous plan on this repo predicted it wrong twice.

- [ ] **Step 11: Run the full gate**

Run: `bash scripts/check.sh`

Expected: `all checks passed`.

- [ ] **Step 12: Commit**

```bash
git add clients/word/src/contextNotice.ts clients/word/src/contextNotice.test.ts clients/word/src/api.ts clients/word/src/components/ChatTab.tsx clients/word/src/components/FindingsTab.tsx clients/word/src/styles.css scripts/check.sh
git commit -m "feat(word): tell the attorney when part of the document was not sent

Pure truncationNotice() formatter plus a notice on both the chat and review
paths, typed through QueryResponse.

Deliberately NOT .status.warning: styles.css documents that amber already means
'this turn wasn't remembered', and that two meanings must not look alike or the
frequent one trains testers to ignore the rare one. Truncation is the most
severe of the three -- memory loss costs convenience, this means the answer may
be legally unsound -- so it gets its own, more serious treatment.

Says 'none of this document' rather than '0%' in the worst case, where the
budget leaves nothing of the contract at all."
```

---

## Task 6: Correct the stale claims and document the work

**Files:**
- Modify: `tests/test_observability.py:321`, `tests/test_skills.py:1431`, `CLAUDE.md`, `docs/wiki.md`

**Interfaces:**
- Consumes: the final values from Tasks 1–5.
- Produces: documentation only.

- [ ] **Step 1: Fix the two stale docstrings**

Both mention the old default and become false with Task 1. They are docstrings, not assertions, so no test fails — which is exactly why they need doing deliberately.

In `tests/test_observability.py`, the docstring currently reading
`The value comes from settings.ollama_num_ctx (default 32768).` becomes:

```python
    The value comes from settings.ollama_num_ctx (default 131072 — pinned to the
    window Spark serves; see config.py for why it must be EQUAL, not larger)."""
```

In `tests/test_skills.py`, the docstring currently reading
`qwen3.6 supports 262k; we pin a project-level default of 32768."""` becomes:

```python
    qwen3.6 supports 262k; we pin a project-level default of 131072 to match the
    window the inference server actually loads — a mismatch in either direction
    forces a 4.7s model reload."""
```

- [ ] **Step 2: Run the suite**

Run: `uv run pytest tests/test_observability.py tests/test_skills.py -q`

Expected: all pass (docstring-only edits).

- [ ] **Step 3: Add the CLAUDE.md gotcha**

`CLAUDE.md` is capped at **≤150 lines** — check with `wc -l CLAUDE.md` first. If adding would exceed the cap, consolidate or drop the lowest-value line rather than appending.

Add under the **Backend** section:

```markdown
- **`ollama_num_ctx` must EQUAL the inference server's window, not merely fit under it.** Ollama reloads the model whenever a request's `num_ctx` differs from the resident one, in **either** direction — asking for 32768 against Spark's resident 131072 forced a 4.7s reload of a 24GB model on every call and contended with every other consumer of that box. It also capped review input at `num_ctx - num_predict_review` = 24,576 tokens against a **measured 25,270-token** MSA review, so Ollama middle-dropped the overflow — removing exactly the playbook/MSA. Two silent bugs, one stale default. KV is cheap (~49.5 MB/1k tokens: qwen3.6 is a hybrid SSM/attention MoE, `full_attention_interval=4` over 40 blocks ⇒ ~10 attention layers), so **memory was never the constraint — prefill latency is** (1,397 tok/s measured). `chat_context_max_chars` is therefore *derived* from a latency ceiling, and **chars/token is 4.89 on real legal text, not 4** (`est_chars_per_token`) — the chars/4 rule overstated by 22% and every budget comment using it was wrong. Verify after deploy: `curl http://<ollama>:11434/api/ps` — `ctx` must equal `ollama_num_ctx`. **`_cap_chat_context` truncates ONLY the document** (a *tail* cut, so liability/indemnity/termination/governing-law/signature blocks go first); it now returns `{doc_chars, kept_chars, kept_pct}` → `state["context_truncated"]` → `report` → a red pane notice, distinct from amber `.status.warning` ("not remembered") on purpose. `contract_review` has no input cap — `llm_caller` **detects** overflow past the headroom and flags it, but deliberately does not truncate.
```

- [ ] **Step 4: Update the wiki**

In `docs/wiki.md`, add a row to "Shipped Since Last Update" naming: the two silent truncation bugs, the pinned window, the derived budget, the measured 4.89 chars/token, the truncation notice on both tabs, and the review-path detection. Refresh the test counts using the real numbers from `bash scripts/check.sh`.

Add to the follow-ups list, from the spec's Follow-ups section: the OpenAI-compatible backend seam; the self-calibrating context gauge; history compaction; cross-session recall; the Qwen3-14B serving fault (7.5 tok/s decode); and that `ollama_num_predict_review = 8192` is **159 s of pure decode** at 51.4 tok/s, making the review path a decode problem rather than a prefill one.

- [ ] **Step 5: Verify the CLAUDE.md cap**

Run: `wc -l CLAUDE.md`

Expected: ≤150. If over, consolidate before committing.

- [ ] **Step 6: Run the full gate**

Run: `bash scripts/check.sh`

Expected: `all checks passed`.

- [ ] **Step 7: Commit**

```bash
git add tests/test_observability.py tests/test_skills.py CLAUDE.md docs/wiki.md
git commit -m "docs: record the context-window gotcha and correct stale claims

Two docstrings cited the old 32768 default and became false with this branch.
They are docstrings, not assertions, so nothing failed -- which is why they
needed doing deliberately.

CLAUDE.md gains the gotcha worth the most to a future reader: num_ctx must
EQUAL the server's window (a mismatch either way reloads a 24GB model),
memory was never the constraint but prefill latency is, and chars/token is
4.89 on real legal text rather than 4."
```

---

## Manual verification (no automated coverage — stated so it is not mistaken for covered)

These cannot be asserted in CI and must be done by hand before the branch is considered done:

1. **The deployed window matches.** `curl http://172.20.0.22:11434/api/ps` — `ctx` must read `131072`. A mismatch is a live-config fact invisible to every test.
2. **Real latency at the new budget.** Re-measure a full grounded MSA chat turn; the design target is ~26.5 s and the ceiling is 30 s.
3. **The notice renders in Word.** Sideload and force a truncation by temporarily setting `CHAT_CONTEXT_MAX_CHARS=20000` in `.env`, restarting, and asking a question about a real MSA. `npx tsc --noEmit` is *not* sufficient for add-in changes.
4. **Review quality with full grounding.** Reviews now send ~25k tokens where Ollama previously dropped some, so the model sees clauses it never saw before. Spot-check a known MSA review — this is the fix working, but it is a behaviour change and should be looked at rather than assumed.
