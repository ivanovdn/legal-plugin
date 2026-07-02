# num_ctx fix report

## Summary

Pins `ollama_num_ctx=32768` on all grounded LLM calls. Without this, Ollama uses
its default context window (~4096 tokens), silently truncating large prompts that
include the playbook bundle + MSA + document + answer. `qwen3.6:latest` supports
262k context; 32768 is a conservative default that fits the assembled chat prompt
(~25k tokens) with ~7k-token headroom for the answer and can be raised per hardware
via the `OLLAMA_NUM_CTX` env var.

---

## Edits

### 1. `config.py` (lines 59–60)

- Added `ollama_num_ctx: int = 32768` setting (immediately above `chat_context_max_chars`).
- Changed `chat_context_max_chars` default from `120000` to `100000` so the assembled
  chat prompt (~25k tokens at ~4 chars/token) fits inside `ollama_num_ctx` (32768 tokens)
  with answer headroom.
- Updated comment on `chat_context_max_chars` to note the token-budget relationship.

### 2. `skills/legal_research.py` (lines 106–131)

- `_build_llm()`: added `num_ctx=settings.ollama_num_ctx` to `ChatOllama(...)` kwargs.
- `_build_json_llm()`: added `num_ctx=settings.ollama_num_ctx` to `ChatOllama(...)` kwargs.
- `_build_agent()` left unchanged per instructions (ReAct agent, not a grounded call).

### 3. `graph/nodes/llm_caller.py` (line 71)

- `"options": {"temperature": 0.0}` → `"options": {"temperature": 0.0, "num_ctx": settings.ollama_num_ctx}`.
- `settings` is already fetched at the top of `llm_caller()` via `get_settings()`.

### 4. Tests added

**`tests/test_skills.py`** — two new tests appended at end of file:

- `test_build_llm_sets_num_ctx`: monkeypatches `OLLAMA_NUM_CTX=12345`, clears cache,
  calls `_build_llm()`, asserts `llm.num_ctx == 12345`.
- `test_build_json_llm_sets_num_ctx`: same pattern for `_build_json_llm()` with
  `OLLAMA_NUM_CTX=8192`.

**`tests/test_observability.py`** — one new test appended at end of file:

- `test_llm_caller_sends_num_ctx_in_options`: monkeypatches `httpx.post` to capture
  the posted JSON, sets `OLLAMA_NUM_CTX=16384`, calls `llm_caller(state)`, asserts
  `captured_json["options"]["num_ctx"] == 16384`.

---

## TDD RED → GREEN evidence

**RED** (tests written before implementation):
```
FAILED tests/test_skills.py::test_build_llm_sets_num_ctx
  AssertionError: assert None == 12345
  where None = getattr(ChatOllama(...), 'num_ctx', None)

FAILED tests/test_skills.py::test_build_json_llm_sets_num_ctx
  AssertionError: assert None == 8192

FAILED tests/test_observability.py::test_llm_caller_sends_num_ctx_in_options
  AssertionError: assert None == 16384
  where None = {'temperature': 0.0}.get('num_ctx')
```

**GREEN** (after implementation):
```
3 passed, 74 deselected, 1 warning in 0.59s
```

---

## ChatOllama attribute confirmation

The RED phase confirmed `ChatOllama` exposes `num_ctx` as an instance attribute when
passed as a kwarg — the assertion `getattr(llm, "num_ctx", None) == 12345` returned
`None` before the fix (kwarg absent) and `12345` after (kwarg present). The model
line `ChatOllama(model='qwen3.6:latest', reasoning=False, temperature=0.0, base_url=...)`
in the error trace confirms the attribute is readable at construction time.

---

## Full-suite count

- Worktree tests (excluding `test_build_playbook.py` which requires gitignored
  `data/contract_review_skills/` unavailable in worktrees): **246 passed, 1 skipped**.
- Main project `tests/`: **262 passed, 1 warning** (all pre-existing tests pass;
  the `chat_context_max_chars` default change does not affect any test because the
  one relevant test sets its own value via `CHAT_CONTEXT_MAX_CHARS=2000`).

---

## Adaptations

- The `test_build_playbook.py` failures in the worktree are pre-existing: those tests
  run `scripts/build_playbook.py` which requires the gitignored `.docx` source file.
  Confirmed by running the same tests in the main checkout (all pass there).
- No changes needed to `_build_agent()` per instructions.

---

## Concerns

None. The fix is strictly additive: Ollama ignores unknown/extra `options` keys, and
the `num_ctx` key is the documented way to set the context window. A lower default
(32768 < 262k) leaves room to raise via env var without redeployment.
