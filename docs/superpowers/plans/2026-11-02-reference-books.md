# Reference books in RAG (Circular 92) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Circular 92 (Dec. 2025) is parsed into a tree, chunked, ingested into its own `legal_books` collection with Qdrant hybrid search, searchable and look-up-able from the research path only, and cited by `ref_id`s that code checks, renders and audits.

**Architecture:** A book-specific front end (`ingest/books/circular92/`: PDF words → lines → labelled nodes with printed pages) feeds a layout-free back end (`ingest/books/`: cross-references, note links, chunks, `ref_id` grammar, citation rendering). A separate Qdrant client with `cloud_inference=True` writes and queries a dense + server-side BM25 collection (`rag/book_store.py`, `rag/book_search.py`). Two tools reach it from the research agent; `risk_assessor` checks cited ids once and stores the result, which `output_formatter` renders and `memory_writer` audits.

**Tech Stack:** Python 3.12, pdfplumber 0.11.9, qdrant-client 1.18.0 against Qdrant 1.17, Ollama `/api/embed` (embeddinggemma), LangGraph 0.6 / langchain-core 0.3, Postgres (testcontainers), pytest.

**Spec:** [2026-10-30-reference-books-design.md](../specs/2026-10-30-reference-books-design.md). Decisions: [docs/decisions/](../../decisions/README.md), 2026-10-30, *reference book(s)*.

**Prototype (read-only reference, stays in the vault):** `/Users/dmytroivanov/obsidian/Obsidian Vault/PROJECTS/legal-plugin/research/reference-books/claude-code/` — `REPORT.md`, `stream.py`, `lines.py`, `fn_check.py`, `parse_book.py`, `labels.py`, `defs101.py`, `notes.py`, `xref.py`, `chunker.py`, `text.py`, `chunks_ch1_ch5.jsonl`. Port the rules into tested code; never import or copy these files.

## Before Task 1

- **Branch.** The decision notes, the spec and this plan are committed on `docs/reference-book-decisions`. Implementation runs on `feat/reference-books`, cut from that branch (owner, 2026-11-02); never on `main`.
- **Local services.** Docker must run (testcontainers starts Postgres, and Qdrant from Task 9). Ollama with `embeddinggemma:latest` and `LLM_MODEL` is needed only in Task 15.
- **Never touch Spark or the VM** in any task. See *Rollout* at the end.

## Global Constraints

- No new dependencies. pdfplumber and qdrant-client are already in `requirements.txt`, `requirements-runtime.txt` and both locks. No fastembed, no model download anywhere (constraint 7).
- All imports at top of file (CLAUDE.md rule 1). No backwards-compat shims: change call sites (rule 5). In tests, reach `skills/legal_research/legal_research.py` with `importlib.import_module`.
- `ref_id` = `<book>-<edition>:<path>`, e.g. `circ92-2025-12:usc17-s122-f-1`. Characters only `[A-Za-z0-9_:-]`. A `ref_id` names a tree node, never a chunk. Point id = `uuid5(POINT_NAMESPACE, chunk_key)`.
- BM25 is computed by Qdrant: `QdrantClient(url=…, cloud_inference=True)` and `models.Document(text=…, model="qdrant/bm25")`; the sparse vector is named `bm25` with `modifier=models.Modifier.IDF`; the dense vector is named `dense` (`QDRANT_VECTOR_DIM`, cosine). The existing `get_qdrant_client()` is not changed.
- Every `/api/embed` request sends `"truncate": False`. Over-length = HTTP 400 whose body contains `context length` (measured 2026-11-02: `{"error":"the input length exceeds the context length"}`).
- The book collection has no `client_id`. Book functions and tools take no `client_id` and no collection argument from the model; they read `BOOK_COLLECTION`.
- `search_books` and `lookup_book` go only into the research agent (`_build_agent`). Not contract generation, not doc-chat, not contract review.
- Tool output never prints a page or the edition label ("Dec. 2025").
- Settings (D12), each a `Settings` field and on `RECORDED` in `api/runtime_facts.py`: `book_collection="legal_books"`, `book_chunk_max_tokens=800`, `book_search_top_k=8`, `book_dense_candidates=30`, `book_sparse_candidates=30`, `book_min_score=0.0`, `book_lookup_max_tokens=6000`.
- Degradation codes, both SILENT (`announced=False`): `book_search_failed`, `embedding_query_truncated`. Declare each in the task that wires it; `scripts/check_degradation_vocabulary.py` fails on a declared-but-unused code.
- A new `LegalAgentState` field is listed in `docs/architecture.md`'s state block in the same commit (`scripts/check_wiki.py`).
- Red first, then mutation-checked (spec, *Verification*): each test fails before its code exists, and fails again when the line it guards is reverted once by hand.
- Tests are flat in `tests/`. Run `uv run pytest tests/ -q` and `uv run python scripts/check_degradation_vocabulary.py` before each commit; `bash scripts/check.sh` before the last.
- One commit per task, `feat(books): …` (or `fix`/`docs`), ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Rulings made while planning

Each is where the spec was silent or, checked against the book, wrong. The spec has been corrected where it was wrong (TRIPs, page range, §506).

1. **TRIPs in Circular 92 is Part II, Section 6 only: Articles 35–38** (app. S, printed pp. 457–458). Examples use `trips-art38-1`.
2. **The body is PDF pages 23–481.** PDF 1–22 is front matter; 482–486 is a folio page, three blanks and the back cover, which would otherwise parse as Appendix T notes.
3. **Scope starts are edition data**, not detected (so a test can parse any page range): see `SCOPE_STARTS` in Task 4. A test checks each listed page opens with that title.
4. **Footnote markers are lifted from non-note lines only**, at any size. On note lines a digits-only word is text.
5. **Labels: kind from the label's characters and the expected next sibling; the indent only breaks ties** (the report's recommended rule). That alone should place §506(a)(3) correctly; an override is added only if it doesn't.
6. **Appendix T** (the deleted definition of "Berne Convention work") is one node: path `usc17-s101-def-berne-convention-work`, kind `repealed`, source_type `usc_title`, reached by lookup only.
7. **`repealed` renders as its statute format plus " (repealed)".**
8. **Section numbers are lowercased in paths** (`usc17-s106a`), as in the prototype; subdivision labels keep their case (`usc17-s122-a-4-B`).
9. **No chunk exceeds the budget.** Any text run over it (a leaf, a lead, a tail, a note, a definition) splits at paragraph starts, then at sentence ends. Keys: `<key>`, `<key>~p2`, `<key>~p3`…, so a split lead is `<id>~lead`, `<id>~lead~p2`.
10. **Golden rows also carry `label` and the node's own `text`**, so a cleaning change shows up as a readable diff.
11. **Cross-references are stored as paths** (`usc18-s2323`, `pl110-403`), not `ref_id`s: they name law, not book nodes. One resolves when its path is a node in the tree.
12. **The page check runs in `risk_assessor` on the raw answer**, and only when the book is in play (the answer cites a book id or this turn retrieved book passages). Before rendering, every page in the answer is the model's. A flag set in `output_formatter` would be too late to route to review.
13. **Citations are resolved once, in `risk_assessor`,** into the per-turn state field `book_citations`. `output_formatter` renders from it and `memory_writer` audits from it, so the flag, the text and the audit can't disagree. If Qdrant fails during resolution, every cited id is flagged "Could not verify a cited reference: <id>" and renders unverified; no new degradation code (D13: an unverified citation is a risk flag).
14. **`BOOK_MIN_SCORE` compares `rerank_score`** when the reranker scored a hit, else Qdrant's fused score.
15. **A real ingest refuses unless the dry-run bar passes.**
16. **The research agent's `ChatOllama` gets `num_ctx=settings.ollama_num_ctx`.** Confirmed by the owner, 2026-11-02. Thinking is left as it is.
17. **The appendix table uses each Act's real Public Law number.** As extracted, the book's notes print `94-533` (app. A), `115-262` (app. K) and `116-26` (app. M); the Acts are 94-553, 115-264 and 116-260. Task 6 checks each against the printed page.
18. **Qdrant tests run against a testcontainers Qdrant** at the image `docker-compose.yml` pins, as Postgres does.
19. **`book_refs_retrieved` keeps recency:** an id retrieved again moves to the end, and the cap keeps the last 500.
20. **Line 3 of a tool result is the chunk's heading path** (`Limitations on exclusive rights: Fair use`), not D7's `§ 107 · …`, which repeats line 2. The chunk text's header is `<label> — <heading path>`.

## Review Focus

1. **The model writes an id bare, in backticks, or followed by a period** (`circ92-2025-12:usc17-s107.`): it is still found, checked and rendered, and the period survives. Tests: Task 3 (`render_response`), Task 12.
2. **Qdrant is down mid-turn:** the book tool returns an error text, the turn still answers, `book_search_failed` is recorded, and any cited id is flagged "Could not verify". Tests: Task 11, Task 12.
3. **A lookup of §115 (22,023 tokens):** output stops at `BOOK_LOOKUP_MAX_TOKENS`, and the rest are listed without a `ref_id:` marker, so they don't count as retrieved. Tests: Task 10, Task 11.
4. **Re-ingesting the same edition:** the point count is unchanged (no duplicates). Tests: Task 9, Task 13.
5. **A client-document research answer that says "p. 3 of the MSA":** no page flag, since the book wasn't used. Test: Task 12.

---

### Task 1: Settings and embedding truncation (D3, D12)

**Files:**
- Modify: `config.py`, `api/runtime_facts.py`, `rag/embeddings.py`, `observability/degradations.py`
- Test: `tests/test_embeddings.py`, `tests/test_config.py`

**Interfaces:**
- Produces: the seven `book_*` settings; `class EmbeddingInputTooLong(ValueError)` with `.index: int` and `.chars: int`; `embed_texts(texts: list[str]) -> list[list[float]]`, raising `EmbeddingInputTooLong`; `embed_query(query: str) -> list[float]`; `prompt_token_count(text: str) -> int | None` (Ollama's `prompt_eval_count` for one text, `None` when over the window); `EMBEDDING_QUERY_TRUNCATED = "embedding_query_truncated"` in `SILENT_REASONS`.

- [ ] **Step 1: Write the failing tests** in `tests/test_embeddings.py`, with `httpx.post` patched on `rag.embeddings` (fake responses with `status_code`, `text`, `json()`):

```python
def test_every_request_sends_truncate_false(): ...
    # embed_texts(["a"]) and embed_query("q"): every call's json has truncate is False
def test_ingest_over_length_raises_naming_the_item():
    # batch call -> 400 '{"error":"the input length exceeds the context length"}';
    # single-item calls: "ok" -> 200, the long one -> 400
    with pytest.raises(EmbeddingInputTooLong) as e:
        embed_texts(["ok", "x" * 9000])
    assert e.value.index == 1 and e.value.chars == 9000
    assert "#1" in str(e.value) and "9000" in str(e.value)
def test_query_over_length_retries_once_truncated_and_records(monkeypatch):
    # first call 400 over-length, second 200; second call's json has truncate is True
    # record_degradation called once with (EMBEDDING_QUERY_TRUNCATED, announced=False)
def test_other_http_errors_still_raise():   # 500 -> httpx.HTTPStatusError, no retry
def test_prompt_token_count():              # 200 {"prompt_eval_count": 4, ...} -> 4; over-length 400 -> None
```

In `tests/test_config.py`: `test_book_settings_defaults` asserts the seven defaults from Global Constraints.

- [ ] **Step 2: Run, expect failures**

Run: `uv run pytest tests/test_embeddings.py tests/test_config.py -q`
Expected: FAIL (`EmbeddingInputTooLong` not defined, missing settings).

- [ ] **Step 3: Implement.**
  - Add the seven settings, with a one-line comment each. `book_min_score`'s comment says it compares the rerank score when the reranker ran, else the fused score, and that 0 = off until the test set calibrates it.
  - Add the seven names to `RECORDED`.
  - In `rag/embeddings.py`, `_ollama_embed(texts, prefix, *, truncate=False)` returns the response.
  - `embed_texts`: on over-length, re-embed one item at a time to find the first over-length item, then raise `EmbeddingInputTooLong`. Message: `"Embedding input #<i> is <n> chars, longer than the embedding model's window; nothing was cut"`.
  - `embed_query`: on over-length, retry once with `truncate=True` and `record_degradation(EMBEDDING_QUERY_TRUNCATED, announced=False, detail=f"{len(query)} chars")`.
  - Any other non-2xx: `raise_for_status()` as today.
  - Declare `EMBEDDING_QUERY_TRUNCATED` in the SILENT block.

- [ ] **Step 4: Run tests and the vocabulary gate**

Run: `uv run pytest tests/ -q && uv run python scripts/check_degradation_vocabulary.py`
Expected: all pass. `tests/test_runtime_facts.py` passes only once all seven settings are on `RECORDED`.

- [ ] **Step 5: Commit** — `fix(rag): embeddings send truncate:false — ingest fails loudly, queries retry and record it`

---

### Task 2: The PDF fixture, the book registry and the overrides file (D10, D11)

**Files:**
- Create: `tests/fixtures/books/circular-92-2025-12.pdf`, `ingest/books/__init__.py`, `ingest/books/registry.py`, `ingest/books/overrides.py`, `ingest/books/circular92/__init__.py`, `ingest/books/circular92/overrides-2025-12.json` (initially `[]`)
- Test: `tests/test_book_registry.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class BookEntry`, with fields `id, title, short_title, edition_id, edition_label, jurisdiction, source, licence, pdf_sha256, front_end, overrides_path` and the property `ref_prefix -> str` (`"circ92-2025-12"`).
  - `BOOKS: dict[str, BookEntry]`; `class UnknownBook(LookupError)`; `class PdfHashMismatch(ValueError)`.
  - `get_book(book_id: str) -> BookEntry`; `verify_pdf(book: BookEntry, pdf_path: Path) -> None`; `book_for_ref(ref_id: str) -> BookEntry`, matching on the `"<id>-<edition_id>:"` prefix and raising `UnknownBook`.
  - `@dataclass(frozen=True) class Override`, with fields `id: str, kind: str, page: int, match: str, value: str, reason: str`, and `OVERRIDE_KINDS = frozenset({"label_level", "hyphen", "xref_target"})`.
  - `load_overrides(path: Path) -> list[Override]`.
  - `class OverrideLedger`, with `__init__(overrides)`, `find(kind: str, page: int, text: str) -> Override | None` (marks the override used) and `unused() -> list[Override]`.

- [ ] **Step 1: Fetch the book.**
  - Download `https://www.copyright.gov/title17/title17.pdf` to `tests/fixtures/books/circular-92-2025-12.pdf`.
  - Check that it has 486 pages, that the front matter names the December 2025 edition, and that PDF page 24 opens §101 (`§101 · Definitions`).
  - **If it is not the Dec. 2025 edition, stop and report.** Every page fact in this plan is for that edition, and an archived copy must come from the owner.
  - Record `shasum -a 256` of the file.

- [ ] **Step 2: Write the failing tests:**

```python
def test_registry_entry():          # BOOKS["circ92"]: short_title "Circular 92", edition_id "2025-12",
                                    # edition_label "Dec. 2025", jurisdiction "US", front_end "circular92",
                                    # licence "public domain, U.S. government work"; ref_prefix == "circ92-2025-12"
def test_unregistered_book_is_refused():   # get_book("nope") raises UnknownBook
def test_hash_mismatch_is_refused(tmp_path):  # verify_pdf(book, other_file) raises PdfHashMismatch naming both hashes
def test_fixture_matches_registry():  # verify_pdf(get_book("circ92"), FIXTURE) does not raise
def test_book_for_ref():              # book_for_ref("circ92-2025-12:usc17-s107").id == "circ92"; "x-2020-01:a" raises
def test_override_needs_a_reason(tmp_path):   # entry with "reason": "" -> ValueError; unknown kind -> ValueError; duplicate id -> ValueError
def test_ledger_reports_unused():     # two overrides, find() one -> unused() == [the other]
def test_shipped_overrides_load():    # load_overrides(get_book("circ92").overrides_path) does not raise
```

- [ ] **Step 3: Run, expect failures.** Run: `uv run pytest tests/test_book_registry.py -q`

- [ ] **Step 4: Implement.**
  - The registry entry:
    - `title`: "Copyright Law of the United States and Related Laws Contained in Title 17 of the United States Code";
    - `source`: the download URL;
    - `pdf_sha256`: from Step 1;
    - `overrides_path`: `ingest/books/circular92/overrides-2025-12.json`, resolved from the repo root.
  - `match` and `value` mean, by kind:
    - `label_level`: `match` is a line prefix, `value` the level;
    - `hyphen`: `match` is `"<word->|<next word>"`, `value` is `join` or `keep`;
    - `xref_target`: `match` is the raw reference text, `value` the target path.

- [ ] **Step 5: Run, expect pass, then commit** — `feat(books): Circular 92 fixture, book registry and overrides file`

---

### Task 3: The book vocabulary — ids, models, citations (pure code)

**Files:**
- Create: `ingest/books/ref_ids.py`, `ingest/books/models.py`, `ingest/books/citations.py`
- Test: `tests/test_book_ref_ids.py`, `tests/test_book_citations.py`

**Interfaces:**
- Consumes: `BookEntry` (Task 2).
- Produces, in `ref_ids.py`:
  - `REF_ID_PATTERN = r"[a-z][a-z0-9]*-\d{4}-\d{2}:[A-Za-z0-9_-]*[A-Za-z0-9]"`;
  - `REF_ID_RE`, which matches only when not preceded by `[A-Za-z0-9_:-]`;
  - `REF_MARKER_RE = re.compile(rf"ref_id:\s*({REF_ID_PATTERN})")`;
  - `POINT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "legal-plugin/reference-books")`;
  - `make_ref_id(book: BookEntry, path: str) -> str`;
  - `split_ref_id(ref_id: str) -> tuple[str, str, str]` (book, edition, path; `ValueError` off-grammar);
  - `chunk_key(ref_id: str, suffix: str = "") -> str`;
  - `point_id(key: str) -> str`;
  - `find_cited_ref_ids(text: str) -> list[str]` (unique, first-seen order);
  - `is_under(path: str, ancestor: str) -> bool`;
  - `slugify_term(term: str) -> str`;
  - `parse_reference(reference: str) -> str | None` (a full `ref_id`, a path, or `None`).
- Produces, in `models.py`:
  - `KINDS = ("rule", "definition", "history_note", "repealed")` and `SOURCE_TYPES = ("usc_title", "uncodified_act", "treaty")`.
  - `@dataclass TextLine(text: str, page: int, marks: list[int] = [], para_start: bool = False)`.
  - `@dataclass Node`, with fields:
    - `path, kind, source_type, jurisdiction, label, heading, scope`;
    - `lead: list[TextLine]`, `children: list[Node]`, `tail: list[TextLine]`;
    - `appendix: str | None = None`, `term: str | None = None`, `note_number: int | None = None`, `annotates: str | None = None`;
    - `xrefs: list[str]`, `history_notes: list[str]` (paths), `effective_date: bool = False`;

    and the methods `walk() -> Iterator[Node]`, `own_lines() -> list[TextLine]`, `all_lines() -> list[TextLine]` and `pages() -> tuple[int, int]`.
  - `@dataclass BookTree(book: BookEntry, nodes: list[Node])`, with `walk()` and `index() -> dict[str, Node]`.
  - `class SubRef(BaseModel)`: `ref_id, label, page_start, page_end`.
  - `class BookChunk(BaseModel)`, with fields:
    - `chunk_key, ref_id, book, edition, path, kind, source_type, jurisdiction, label, heading`;
    - `appendix: str | None`, `page_start: int`, `page_end: int`;
    - `text` (header + "\n" + body: embedded), `body` (BM25 text, also what the tools show), `tokens: int`, `order: int`;
    - `ancestor_ref_ids: list[str]`, `sub_refs: list[SubRef]`, `sub_ref_ids: list[str]`;
    - `xrefs: list[str]`, `history_notes: list[str]`, `effective_date_notes: list[str]` (both prefixed `ref_id`s).

    `point_id` is a property.
- Produces, in `citations.py`:
  - `page_span(start: int, end: int) -> str`;
  - `render_citation(*, label, kind, source_type, appendix, page_start, page_end, book: BookEntry) -> str`;
  - `render_response(text: str, citations: Mapping[str, str]) -> str`;
  - `find_stated_pages(text: str) -> list[str]`;
  - `UNVERIFIED = "[unverified reference: {ref_id}]"`.

- [ ] **Step 1: Write the failing tests:**

```python
# test_book_ref_ids.py
def test_round_trip():  # split_ref_id(make_ref_id(book, "usc17-s122-a-4-B")) == ("circ92", "2025-12", "usc17-s122-a-4-B")
def test_one_token_in_prose():
    t = "Fair use (ref_id: circ92-2025-12:usc17-s107). See `circ92-2025-12:ch1-n40`, and circ92-2025-12:usc17-s101-def-joint-work."
    assert find_cited_ref_ids(t) == ["circ92-2025-12:usc17-s107", "circ92-2025-12:ch1-n40",
                                     "circ92-2025-12:usc17-s101-def-joint-work"]
def test_chunk_keys_and_point_ids():  # chunk_key(r, "~lead") == r + "~lead"; point_id is uuid5 and stable; differs per key
def test_is_under():  # ("usc17-s101-def-joint-work","usc17-s101") True; ("usc17-s1010","usc17-s101") False; equal True
@pytest.mark.parametrize("text,expected", [
    ("§ 107", "usc17-s107"), ("section 107", "usc17-s107"), ("§ 106A", "usc17-s106a"),
    ("17 U.S.C. §122(f)(1)", "usc17-s122-f-1"), ("17 U.S.C. 122(a)(4)(B)", "usc17-s122-a-4-B"),
    ("18 U.S.C. § 2319", "usc18-s2319"), ("section 101 'joint work'", "usc17-s101-def-joint-work"),
    ("§101 (“joint work”)", "usc17-s101-def-joint-work"), ("TRIPs art. 38(1)", "trips-art38-1"),
    ("TRIPs Article 36", "trips-art36"), ("Pub. L. No. 105-304, §101", "pl105-304-s101"),
    ("ch. 1 n.3", "ch1-n3"), ("app. B n.2", "appB-n2"),
    ("circ92-2025-12:usc17-s107", "circ92-2025-12:usc17-s107"), ("usc17-s107", "usc17-s107"),
    ("what is fair use", None)])
def test_parse_reference(text, expected): ...
def test_slugify_term():  # "joint work" -> "joint-work"; "Berne Convention work" -> "berne-convention-work"

# test_book_citations.py — book = get_book("circ92")
@pytest.mark.parametrize("kw,expected", [
  (dict(label="17 U.S.C. §122(f)(1)", kind="rule", source_type="usc_title", appendix=None, page_start=143, page_end=143),
   "17 U.S.C. §122(f)(1) — Circular 92 (Dec. 2025), p. 143"),
  (dict(label="17 U.S.C. §101 (“joint work”)", kind="definition", source_type="usc_title", appendix=None, page_start=4, page_end=4),
   "17 U.S.C. §101 (“joint work”) — Circular 92 (Dec. 2025), p. 4"),
  (dict(label="18 U.S.C. §2319", kind="rule", source_type="usc_title", appendix="N", page_start=428, page_end=428),
   "18 U.S.C. §2319 — Circular 92 (Dec. 2025), app. N, p. 428"),
  (dict(label="Digital Millennium Copyright Act, Pub. L. No. 105-304, §101 (1998)", kind="rule",
        source_type="uncodified_act", appendix="B", page_start=371, page_end=371),
   "Digital Millennium Copyright Act, Pub. L. No. 105-304, §101 (1998) — Circular 92 (Dec. 2025), app. B, p. 371"),
  (dict(label="ch. 1 n.3", kind="history_note", source_type="usc_title", appendix=None, page_start=146, page_end=146),
   "Circular 92 (Dec. 2025), ch. 1 n.3, p. 146 (amendment history, not statutory text)"),
  (dict(label="TRIPs Agreement, art. 38(1)", kind="rule", source_type="treaty", appendix="S", page_start=458, page_end=458),
   "TRIPs Agreement, art. 38(1) — Circular 92 (Dec. 2025), app. S, p. 458 (treaty, not U.S. law)"),
  (dict(label="17 U.S.C. §509", kind="repealed", source_type="usc_title", appendix=None, page_start=206, page_end=206),
   "17 U.S.C. §509 — Circular 92 (Dec. 2025), p. 206 (repealed)"),
])
def test_render_citation(kw, expected): assert render_citation(book=book, **kw) == expected
@pytest.mark.parametrize("a,b,expected", [(20,20,"p. 20"), (143,144,"pp. 143–44"), (8,9,"pp. 8–9"),
                                          (99,101,"pp. 99–101"), (199,201,"pp. 199–201")])
def test_page_span(a, b, expected): ...
def test_render_response_forms():
    c = {"circ92-2025-12:usc17-s107": "CITE"}
    assert render_response("x (ref_id: circ92-2025-12:usc17-s107).", c) == "x (CITE)."
    assert render_response("x `ref_id: circ92-2025-12:usc17-s107`", c) == "x CITE"
    assert render_response("x ref_id: `circ92-2025-12:usc17-s107`.", c) == "x CITE."
    assert render_response("x circ92-2025-12:usc17-s107, y", c) == "x CITE, y"
    assert render_response("x ref_id: circ92-2025-12:usc17-s999", c) == "x [unverified reference: circ92-2025-12:usc17-s999]"
def test_find_stated_pages():
    assert find_stated_pages("see p. 12 and pp. 12–13, page 40") == ["p. 12", "pp. 12–13", "page 40"]
    assert find_stated_pages("Pub. L. No. 105-304; §512(p); 17 U.S.C. §107") == []
```

- [ ] **Step 2: Run, expect failures.** Run: `uv run pytest tests/test_book_ref_ids.py tests/test_book_citations.py -q`

- [ ] **Step 3: Implement.**
  - Grammar of paths:
    - `usc<title>-s<section, lowercased>[-<label>…]`;
    - `usc17-s101-def-<slug>`;
    - `pl<congress>-<number>[-s<section>[-<label>…]]`;
    - `trips-art<n>[-<para>…]`;
    - `ch<n>-n<m>`;
    - `app<Letter>-n<m>`.
  - `parse_reference` defaults the title to 17 and accepts `§`, `sec.`, `section`, with or without `U.S.C.`.
  - `render_citation`:
    - history notes: `<short_title> (<edition_label>), <label>, <pages> (amendment history, not statutory text)`;
    - everything else: `<label> — <short_title> (<edition_label>), [app. X, ]<pages>`, then ` (treaty, not U.S. law)` for treaties or ` (repealed)` for `repealed`.
  - `page_span` abbreviates the end page to its last two digits when it has the same number of digits and only those two differ.
  - `render_response` replaces each id found by `find_cited_ref_ids`, together with an optional `ref_id:` marker and backticks around either, with `citations[id]`, or with `UNVERIFIED` when the id is absent.
  - `find_stated_pages` matches, case-insensitively, `\bpp?\.\s?\d+(?:\s?[–-]\s?\d+)?` and `\bpages?\s+\d+(?:\s?[–-]\s?\d+)?`.

- [ ] **Step 4: Run, expect pass, then commit** — `feat(books): ref_id grammar, book models and citation rendering`

---

### Task 4: Front end, part 1 — PDF lines (Circular 92)

**Files:**
- Create: `ingest/books/circular92/layout.py`, `ingest/books/circular92/text.py`
- Modify: `tests/conftest.py`, adding a session fixture `circ92_pdf` that returns the fixture path
- Test: `tests/test_circ92_layout.py`

**Interfaces:**
- Produces:
  - `BODY_PDF_PAGES = range(23, 482)` and `PAGE_OFFSET = 22`.
  - `SCOPE_STARTS: tuple[tuple[int, str], ...]` = ((23,"Chapter 1"), (189,"Chapter 2"), (195,"Chapter 3"), (207,"Chapter 4"), (221,"Chapter 5"), (243,"Chapter 6"), (247,"Chapter 7"), (259,"Chapter 8"), (285,"Chapter 9"), (299,"Chapter 10"), (313,"Chapter 11"), (315,"Chapter 12"), (331,"Chapter 13"), (347,"Chapter 14"), (359,"Chapter 15"), (389,"Appendix A"), (393,"Appendix B"), (399,"Appendix C"), (403,"Appendix D"), (405,"Appendix E"), (407,"Appendix F"), (427,"Appendix G"), (431,"Appendix H"), (433,"Appendix I"), (435,"Appendix J"), (437,"Appendix K"), (443,"Appendix L"), (445,"Appendix M"), (447,"Appendix N"), (461,"Appendix O"), (467,"Appendix P"), (469,"Appendix Q"), (471,"Appendix R"), (479,"Appendix S"), (481,"Appendix T")).
  - `@dataclass RawLine(pdf_page: int, page: int, scope: str, x0: float, top: float, text: str, marks: list[int], kind: str, font: tuple[str, float], fonts: list[str])`, where `kind` is one of `body`, `heading`, `title`, `toc`, `note`, `notes_heading`.
  - `extract_lines(pdf_path: Path, pdf_pages: Iterable[int] = BODY_PDF_PAGES) -> list[RawLine]`.
  - `class HyphenDecider`, with `__init__(lines: Sequence[RawLine], ledger: OverrideLedger)` and `decide(word_end: str, next_word: str, page: int) -> str` (`"join"`, `"keep"` or `"number"`), and the property `undecided -> list[str]`.
  - `clean_join(lines: Sequence[TextLine], hyphens: HyphenDecider) -> str`.
  - `fix_small_caps(text: str) -> str`.

- [ ] **Step 1: Write the failing tests.** They run on the fixture, a few pages at a time:

```python
def test_scope_starts_open_with_their_title(circ92_pdf):  # for each (pdf, name): a line on that page,
                                                          # kind "title", text == name
def test_printed_page_is_pdf_minus_22(circ92_pdf):        # every line from pdf 42 has page 20
def test_running_head_dropped_and_marker_lifted(circ92_pdf):
    first = [l for l in extract_lines(circ92_pdf, [24]) if l.kind != "toc"][0]
    assert first.text == "§101 · Definitions" and first.marks == [2] and first.kind == "heading"
def test_marker_lifted_at_any_size(circ92_pdf):           # pdf 428 (app. G, printed 406): a non-note line has marks == [2]
def test_note_lines_keep_digits(circ92_pdf):              # pdf 446: a note line contains "Pub. L. No. 116-26, 134 Stat. 1182"
def test_scopes(circ92_pdf):                              # lines of pdf 221 scope "Chapter 5"; pdf 479 "Appendix S"
def test_number_breaks_rejoin(): clean_join([TL("Pub. L. No. 105-"), TL("304, 112 Stat.")], d) == "Pub. L. No. 105-304, 112 Stat."
def test_line_end_hyphen_joins_when_safe():   # decider fed lines where "spaces" occurs mid-line elsewhere: "the spac-"+"es" -> "the spaces"
def test_line_end_hyphen_keeps_compound():    # "non-dramatic" occurs mid-line elsewhere and "nondramatic" does not -> keep
def test_undecided_hyphen_uses_override():    # neither form elsewhere: override "hyph-|en" keep -> kept; no override -> join, listed in undecided
def test_small_caps():  assert fix_small_caps("U .S .C .") == "U.S.C."; assert fix_small_caps("( DMCA )") == "(DMCA)"
```

- [ ] **Step 2: Run, expect failures.** Run: `uv run pytest tests/test_circ92_layout.py -q`

- [ ] **Step 3: Implement**, porting the prototype rules from `extract.py`, `lines.py`, `stream.py` and `text.py`. The constants:
  - **Words:** `page.extract_words(extra_attrs=["fontname", "size"], keep_blank_chars=False, use_text_flow=True, x_tolerance=3)`, with each font name taken after its `+`.
  - **Lines:** a word joins a line when its top is within 2.0 of the line's top. A `MinionPro-Regular` word under 10.4 pt that overlaps the line vertically by more than 2 also joins it.
  - **Running heads and folios:** the line's first word is a `TheMixOsF*` font under 8 pt; the line is dropped.
  - **Markers:** a word matching `\d{1,3}` in `MinionPro-Regular`, at any size, on a line that isn't a note line. It is removed from the text and added to `marks`.
  - **Line kinds:**
    - `notes_heading`: the text ends `· Notes` and the first word is `TheMixOsF-Bold`. Every later line in that scope is a `note`.
    - `title`: the first word is `TheMixOsF-ExtraBold`.
    - `heading`: the first word is `TheMixOsF-Bold`, `MinionC-Bold` or `MinionC-BoldItalic`, at 10 pt or more.
    - `toc`: three or more words made only of spaces and dots, or `\.{6,}`, or the text starts with `section page`.
    - Everything else is `body`.
  - **Text:** NFKC, then `U\s?\.S\s?\.C\s?\.` → `U.S.C.`, then `\s+([.,;:])` → `\1`.
  - **Hyphens:** use the evidence of the whole run of lines given to the decider. A form found mid-line elsewhere is evidence for it. Evidence for one form only decides; evidence for both or neither goes to an override, and otherwise defaults to `join` and is listed in `undecided`. A digit before the hyphen and a digit after it is `number`: keep the hyphen and add no space.
  - **Small caps:** collapse stray spaces inside acronyms set in a font whose name contains `SC`, and inside parentheses around one.

- [ ] **Step 4: Run, expect pass, then commit** — `feat(books): Circular 92 layout front end — lines, markers, scopes, text cleaning`

---

### Task 5: Front end, part 2 — the Title 17 tree

**Files:**
- Create: `ingest/books/circular92/labels.py`, `ingest/books/circular92/title17.py`
- Modify: `tests/conftest.py`, adding a session fixture `circ92_ch1_ch5_lines`, which is `extract_lines(circ92_pdf, [*range(23, 189), *range(221, 243)])`, and a session fixture `ch1_ch5_nodes`: the index (`path → Node`, walking every subtree) of the nodes `title17_nodes` builds from those lines
- Test: `tests/test_circ92_title17.py`

**Interfaces:**
- Consumes: `RawLine`, `extract_lines`, `HyphenDecider`, `clean_join` (Task 4); `Node`, `TextLine` (Task 3); `OverrideLedger` (Task 2).
- Produces:
  - `SEQ: dict[int, list[str]]`, the label sequences for levels 1–7 from the prototype's `labels.py`;
  - `resolve_level(label: str, stack: dict[int, str], x_level: int) -> int`;
  - `title17_nodes(lines: list[RawLine], hyphens: HyphenDecider, ledger: OverrideLedger) -> tuple[list[Node], list[str]]`. The nodes are the sections in order, with children, own lines as `TextLine`s carrying `marks` and `para_start`, and kinds `rule`, `definition` or `repealed`. The strings are `out_of_sequence`, for the report (Task 13): each names a label line (page and text) that no rule or override placed. `Node.heading` is the title as printed after the `·` (`Limitations on exclusive rights: Fair use`).

- [ ] **Step 1: Write the failing tests:**

```python
@pytest.mark.parametrize("label,stack,x,expected", [
    ("i", {1: "h"}, 4, 1),                   # sequence says letter i after h
    ("i", {1: "a", 2: "3", 3: "A"}, 5, 4),   # roman i under (A), even though set one level deep
    ("A", {1: "a", 2: "3"}, 4, 3),           # unambiguous class beats the indent
    ("v", {1: "u"}, 4, 1), ("ii", {1: "a", 2: "1", 3: "A", 4: "i"}, 4, 4)])
def test_resolve_level(label, stack, x, expected): ...
def test_sections_in_ch1_ch5(ch1_ch5_nodes):   # 25 + 13 = 38 section nodes, in order
def test_fair_use(ch1_ch5_nodes):   # usc17-s107: label "17 U.S.C. §107", heading "Limitations on exclusive rights: Fair use",
                              # pages (20, 20), children usc17-s107-1 .. usc17-s107-4 (the four factors);
                              # own text contains "sections 106 and 106A" (the prototype left "106 A")
def test_definitions(ch1_ch5_nodes):  # usc17-s101 has 54 children of kind "definition" (lead = the intro sentence);
                              # usc17-s101-def-joint-work: term "joint work", label "17 U.S.C. §101 (“joint work”)"
def test_label_case_kept(ch1_ch5_nodes):        # "usc17-s122-a-4-B" in index
def test_inline_labels_after_heading_dash(ch1_ch5_nodes):  # usc17-s506-a and usc17-s506-a-1 both exist
def test_506_a_3_fixed(ch1_ch5_nodes):          # usc17-s506-a-3-A, -A-i, -A-ii, -B, -B-i, -B-ii all exist; none at -a-3-A-i-… deeper
def test_repealed_stub(ch1_ch5_nodes):          # usc17-s509: kind "repealed", no lines, label "17 U.S.C. §509"
def test_no_unplaced_labels(circ92_ch1_ch5_lines):   # title17_nodes(...)[1] == []
```

- [ ] **Step 2: Run, expect failures.** Run: `uv run pytest tests/test_circ92_title17.py -q`

- [ ] **Step 3: Implement**, porting `parse_book.py` (`sections`, `blocks`, `LEAD`, `INLINE`, `XREF_TAIL`, `END_OK`, `XREF_AFTER`) and `defs101.py`.
  - **Section heading:** `§\s*(\d+[A-Za-z]?)\s*·\s*(.*)`. Following `heading` lines continue the title.
  - **Level from the indent:** `round((x0 - 54) / 12)`.
  - **`resolve_level`:** a label whose character class has one level takes that level. For an ambiguous label (`i v x I V X ii II vv xx`), take the level whose expected next sibling it is. If both or neither fit, take the level nearer `x_level`.
  - **A `label_level` override** is applied before the rule.
  - **§101 definitions:** a definition starts at x0 between 62 and 67, with text that doesn't start with `(`, a quoted term (`“\s?([^”’]+?)\s?(”|’’)`) in its first 70 characters, and a previous line ending in `[.:;—]`.
  - **Labels:** `17 U.S.C. §<num><(label)…>`, and for a definition `17 U.S.C. §101 (“<term>”)`.
  - **`para_start`:** the line's x0 is more than 6 to the right of the previous line's.

- [ ] **Step 4: Run, expect pass, then commit** — `feat(books): Title 17 tree — sections, labelled subdivisions, §101 definitions`

---

### Task 6: Front end, part 3 — history notes and appendices

**Files:**
- Create: `ingest/books/circular92/notes.py`, `ingest/books/circular92/appendices.py`
- Test: `tests/test_circ92_notes_appendices.py`

**Interfaces:**
- Consumes: Tasks 3–5.
- Produces:
  - `EFFECTIVE_DATE_RE = re.compile(r"tak(e|es|en) effect|took effect|effective (date|upon|on)", re.I)`;
  - `history_notes(lines: list[RawLine], placed: list[Node], hyphens) -> list[Node]`, which gives notes of kind `history_note`, paths `ch<n>-n<m>` or `app<L>-n<m>`, labels `ch. 1 n.3` or `app. B n.2`, `annotates` (the path of the node whose own lines, or heading, hold the marker; `None` for a chapter title or table of contents) and `effective_date`;
  - `@dataclass(frozen=True) AppendixSpec(letter, source_type, jurisdiction, prefix, act_name, pub_law, year, reason)`;
  - `APPENDICES: dict[str, AppendixSpec]` for A–T;
  - `appendix_nodes(lines: list[RawLine], hyphens, ledger) -> list[Node]`.

| App | `source_type` | Path prefix | `act_name` | `pub_law` / year |
|---|---|---|---|---|
| A | uncodified_act | `pl94-553` | Copyright Act of 1976 | 94-553 / 1976 (book's n.1 prints 94-533) |
| B | uncodified_act | `pl105-304` | Digital Millennium Copyright Act | 105-304 / 1998 |
| C | uncodified_act | `pl108-419` | Copyright Royalty and Distribution Reform Act of 2004 | 108-419 / 2004 |
| D | uncodified_act | `pl108-447` | Satellite Home Viewer Extension and Reauthorization Act of 2004 | 108-447 / 2004 |
| E | uncodified_act | `pl108-482` | Intellectual Property Protection and Courts Amendments Act of 2004 | 108-482 / 2004 |
| F | uncodified_act | `pl110-403` | Prioritizing Resources and Organization for Intellectual Property Act of 2008 | 110-403 / 2008 |
| G | uncodified_act | `pl111-175` | Satellite Television Extension and Localism Act of 2010 | 111-175 / 2010 |
| H | uncodified_act | `pl113-144` | Unlocking Consumer Choice and Wireless Competition Act | 113-144 / 2014 |
| I | uncodified_act | `pl113-200` | STELA Reauthorization Act of 2014 | 113-200 / 2014 |
| J | uncodified_act | `pl115-261` | Marrakesh Treaty Implementation Act | 115-261 / 2018 (no note in the book) |
| K | uncodified_act | `pl115-264` | Orrin G. Hatch–Bob Goodlatte Music Modernization Act | 115-264 / 2018 (n.1 prints 115-262) |
| L | uncodified_act | `pl116-94` | Satellite Television Community Protection and Promotion Act of 2019 | 116-94 / 2019 |
| M | uncodified_act | `pl116-260` | Copyright Alternative in Small-Claims Enforcement Act of 2020 | 116-260 / 2020 (n.1 prints 116-26) |
| N | usc_title | `usc18` | — | — |
| O | usc_title | `usc28` | — | — |
| P | usc_title | `usc44` | — | — |
| Q | uncodified_act | `pl100-568` | Berne Convention Implementation Act of 1988 | 100-568 / 1988 |
| R | uncodified_act | `pl103-465` | Uruguay Round Agreements Act | 103-465 / 1994 |
| S | treaty | `trips` | TRIPs Agreement | — (jurisdiction `international`) |
| T | usc_title | `usc17-s101-def-berne-convention-work` | — | ruling 6: one `repealed` node |

All jurisdictions are `US` except S.

- [ ] **Step 1: Write the failing tests.** They run on PDF pages 389–481, plus the ch.1 + ch.5 fixture for the notes:

```python
def test_note_counts(ch1_ch5_notes):          # 65 notes in Chapter 1, 12 in Chapter 5, numbered 1..n in order
def test_note_annotates(ch1_ch5_notes):        # ch5-n8 annotates "usc17-s509"; its label "ch. 5 n.8"
def test_effective_date_flag(ch1_ch5_notes):   # ch1-n13 effective_date True, annotates "usc17-s101-def-international-agreement-5"
                                               # (the prototype said §101(5)); exactly 17 of the 77 flagged
def test_every_appendix_parsed(app_nodes):     # each of A..T yields >= 1 node with APPENDICES[L].source_type
def test_trips(app_nodes):                     # trips-art35 .. trips-art38: source_type "treaty", jurisdiction "international",
                                               # label of trips-art38 == "TRIPs Agreement, art. 38"
def test_other_titles(app_nodes):              # usc18-s2318, usc18-s2319, usc28-s137, usc44-s2117 exist, label "18 U.S.C. §2319"
def test_acts(app_nodes):                      # pl105-304-s101: label "Digital Millennium Copyright Act, Pub. L. No. 105-304, §101 (1998)"
def test_appendix_t(app_nodes):                # usc17-s101-def-berne-convention-work: kind "repealed", appendix "T"
def test_appendix_g_note_2(app_notes):         # appG-n2 exists, and its marker (10.2 pt, printed p. 406) is linked
def test_markers_match_notes_in_appendices(app_lines, app_notes):  # per appendix scope: lifted markers == note numbers, in order
```

- [ ] **Step 2: Check the three printed Public Law numbers.** Render printed pp. 367, 415 and 423 (PDF 389, 437 and 445; their notes run onto the following pages) with `pdfplumber` `page.to_image(resolution=150)`. Look at note 1 in each. Record what the book prints in `AppendixSpec.reason`, for example `"note 1 prints 94-533; the Act is Pub. L. 94-553 (90 Stat. 2541)"`. If a printed number turns out right, use it and drop the reason.

- [ ] **Step 3: Run, expect failures.** Run: `uv run pytest tests/test_circ92_notes_appendices.py -q`

- [ ] **Step 4: Implement.**
  - **Note starts**, from the prototype's `notes.py`: `^(\d{1,3})\s?\.\s`, where the number is the next one expected and x0 < 70. Other lines continue the current note.
  - **Appendix section headings** come in five styles:
    - `Sec. 101 · Short Title.`
    - `Section 1 · Short Title.`
    - `Sec. 1. Short Title`
    - `§ 2318 · Trafficking…`
    - `Article 35 · Relation to IPIC Treaty`

    `Title …`, `Part …`, `Chapter …`, `DIVISION …` and `* * *` headings are context only.
  - **Subdivision labels** reuse `resolve_level`.
  - **Labels:**
    - Acts: `<act_name>, Pub. L. No. <pub_law>, §<num><labels> (<year>)`;
    - other titles: `<t> U.S.C. §<num><labels>`;
    - TRIPs: `TRIPs Agreement, art. <n>(<para>)`.

- [ ] **Step 5: Run, expect pass, then commit** — `feat(books): history notes and appendices A–T`

---

### Task 7: Back end — tree assembly, cross-references, note links, the golden file

**Files:**
- Create: `ingest/books/xrefs.py`, `ingest/books/tree.py`, `ingest/books/golden.py` (with `__main__`), `tests/fixtures/books/circ92-ch1-ch5.jsonl`
- Modify: `ingest/books/circular92/__init__.py`
- Test: `tests/test_book_xrefs.py`, `tests/test_book_golden.py`

**Interfaces:**
- Consumes: Tasks 2–6.
- Produces:
  - `@dataclass XrefContext(title: int, section: str | None, path_labels: tuple[str, ...], label_levels: tuple[int, ...], term: str | None = None)`;
  - `find_xrefs(text: str, ctx: XrefContext, ledger: OverrideLedger | None = None) -> list[str]`, giving target paths in order, deduplicated;
  - `parse_circular92(pdf_path: Path, book: BookEntry, pdf_pages: Iterable[int] = BODY_PDF_PAGES) -> ParseResult`, where `@dataclass ParseResult(tree: BookTree, ledger: OverrideLedger, hyphens: HyphenDecider, out_of_sequence: list[str], markers: dict[str, list[int]], notes: dict[str, list[int]])`;
  - `link_notes(tree)`, which fills `Node.history_notes` from the notes' `annotates`;
  - `xref_resolution(tree) -> tuple[int, int, list[tuple[str, str]]]`, giving the total, the number resolved and the misses as `(from path, target)`;
  - `golden_rows(tree: BookTree, scopes: set[str]) -> list[dict]`. Each row, in document order: `{"ref_id", "kind", "source_type", "label", "pages": [first, last], "xrefs", "history_notes", "effective_date", "text"}`. `history_notes` holds prefixed ids; `effective_date` is a bool for notes and `null` otherwise; `text` is the own text, cleaned.

- [ ] **Step 1: Write the failing tests:**

```python
# test_book_xrefs.py, ported from the prototype's xref.py cases
def test_relative_subsection():     # "clause (2) of this subsection" in usc17-s504-c-1 -> "usc17-s504-c-2"
def test_explicit_parent():         # "paragraph (1) of subsection (a)" in §106A -> "usc17-s106a-a-1"
def test_other_title():             # "section 2319 of title 18" -> "usc18-s2319"
def test_title_after_chapter():     # "section 2323, chapter 113 of title 18, United States Code" -> "usc18-s2323"  (ch.5 n.8 bug)
def test_usc_in_parentheses():      # "section 45 of the Act entitled “An Act to provide for … trademarks …” (15 U.S.C. 1127)"
                                    #   -> ["usc15-s1127"] only  (§504(c) bug)
def test_public_law():              # "Pub. L. No. 110-403" -> "pl110-403"; "section 101 of Public Law 105-304" -> "pl105-304-s101"
def test_definition_relative():     # in §101 "work made for hire", "paragraph (2)" -> "usc17-s101-def-work-made-for-hire-2"
def test_named_act_without_number(): # "section 1011(d) of the Intellectual Property and Communications Omnibus Reform Act of 1999"
                                    #   -> no path; listed in xref_resolution's misses as unparsed
# test_book_golden.py — session fixture parses ch.1 + ch.5 once
def test_golden_ch1_ch5(ch1_ch5_parse):   # golden_rows(tree, {"Chapter 1", "Chapter 5"}) == rows of the fixture file;
                                          # on mismatch, the assertion message names the first differing ref_id and field
def test_known_bugs_fixed(ch1_ch5_parse):
    # ch5-n8 xrefs contain "usc18-s2323", not "usc17-s2323"
    # usc17-s504-c: "usc15-s1127" in xrefs, no target containing "Act"
    # the six §506(a)(3) paths exist
def test_note_links(ch1_ch5_parse):       # usc17-s509 history_notes == ["circ92-2025-12:ch5-n8"]
```

- [ ] **Step 2: Run, expect failures.** Run: `uv run pytest tests/test_book_xrefs.py tests/test_book_golden.py -q`. The golden file doesn't exist yet.

- [ ] **Step 3: Implement** `xrefs.py` (porting `xref.py`, with the two fixes and the `xref_target` override), `tree.py`, `parse_circular92` and `golden.py`.
  - `python -m ingest.books.golden <pdf> <out.jsonl>` writes the ch.1 + ch.5 rows.
  - The prototype's self-reference rule (dropping targets under the node's own path) does not apply. The golden file records every target, including relative ones.

- [ ] **Step 4: Generate the golden file and cross-check it against the prototype.** This is the oracle, so it is checked against an independent output before it is committed. Write a scratch script, not committed, that loads the vault's `chunks_ch1_ch5.jsonl` and checks:
  - **Every prototype `ref_id` is a node.** Renaming: `c92-chN-nM` → `chN-nM`; strip `-partN`; `usc17-s…` unchanged; then prefix `circ92-2025-12:`.
  - **Same first page, and the same kind.** Kinds map `history note` → `history_note` and `repealed stub` → `repealed`.
  - **Prototype targets are kept.** Each prototype row's xref targets (`17 U.S.C. §106(a)` → `usc17-s106-a`, and so on) are a subset of the union of xrefs over that node's subtree. The two known-bug rows are the exceptions.

  Every difference must be explained in the task report: a prototype bug or a deliberate rule change. Also put the 17 effective-date notes in the report (`ref_id` and the matching phrase) for the owner's review.

- [ ] **Step 5: Run, expect pass, then commit** — `feat(books): cross-references, note links and the ch.1 + ch.5 golden file`

---

### Task 8: The chunker and the token counter (D4)

**Files:**
- Create: `ingest/books/chunker.py`, `ingest/books/tokens.py`
- Test: `tests/test_book_chunker.py`

**Interfaces:**
- Consumes: `BookTree`, `Node`, `BookChunk`, `SubRef`, `make_ref_id`, `chunk_key` (Task 3); `prompt_token_count` (Task 1).
- Produces:
  - `TokenCounter = Callable[[str], int | None]`, where `None` means over the embedding window;
  - `class CachedTokenCounter`, with `__init__(count: TokenCounter)` and `__call__(text: str) -> int | None`, caching by text;
  - `ollama_token_counter() -> CachedTokenCounter`;
  - `chunk_tree(tree: BookTree, *, budget: int, count_tokens: TokenCounter) -> list[BookChunk]`.

- [ ] **Step 1: Write the failing tests.** Use hand-built trees and a stub counter, `words = lambda t: len(t.split())`:

```python
def test_small_node_is_one_chunk():       # key == ref_id; sub_refs = every descendant with its own pages; text == header + "\n" + body
def test_header():                        # "17 U.S.C. §122(f) — Limitations… › Secondary transmissions…" (label — section heading › parents)
def test_over_budget_splits_by_structure():   # parent with lead + 2 children over budget ->
                                              # keys [<p>~lead, <child1>, <child2>]; children carry the lead's heading as context
def test_bare_heading_lead_is_context_only(): # lead that is only "(f) Heading.—" -> no ~lead chunk
def test_tail_chunk():                    # text after the last child -> "<p>~tail"
def test_leaf_over_budget_splits_at_sentences():   # keys <id>, <id>~p2; every chunk's tokens <= budget
def test_note_splits_at_paragraphs():     # para_start lines start parts first
def test_none_counts_as_over_budget():    # counter returns None for long text -> split
def test_definitions_one_chunk_each():    # §101 shape -> one chunk per definition + one for the intro
def test_repealed_node_has_a_body():      # body == the heading line ("[Repealed]"), never empty (BM25 text)
def test_budget_moves_boundaries_not_ids():   # budget 50 vs 500: different chunk counts, every chunk.ref_id is a node path
def test_payload_links():                 # ancestor_ref_ids (self → section); history_notes / effective_date_notes prefixed;
                                          # effective_date_notes ⊆ history_notes; order strictly increasing; keys unique
def test_cached_counter_counts_each_text_once()
```

- [ ] **Step 2: Run, expect failures.** Run: `uv run pytest tests/test_book_chunker.py -q`

- [ ] **Step 3: Implement**, porting `split()` from the prototype's `chunker.py`, with ruling 9 for the split parts.
  - **History-note chunks:** the header is `<label> — amendment history, not statutory text — on <annotated node's label>`, or the chapter or appendix name when `annotates` is `None`. The `heading` payload is `amendment history, not statutory text`.
  - **Treaty chunks:** `(treaty, not U.S. law)` goes at the end of the header.
  - **`tokens`:** the count of `text`.

- [ ] **Step 4: Run, expect pass, then commit** — `feat(books): chunker — node-stable ids, budget as a setting, counted by the embedding model`

---

### Task 9: The book store in Qdrant (D2)

**Files:**
- Create: `rag/book_store.py`
- Modify: `tests/conftest.py`, adding the fixtures `qdrant_url` (session) and `book_qdrant` (per test)
- Test: `tests/test_book_store.py`

**Interfaces:**
- Consumes: `BookChunk` (Task 3); the settings (Task 1).
- Produces:
  - `get_book_client() -> QdrantClient`, a singleton built with `cloud_inference=True`, and `reset_book_client() -> None`;
  - `BOOK_PAYLOAD_INDEXES = ("ref_id", "book", "edition", "kind", "source_type", "jurisdiction", "ancestor_ref_ids", "sub_ref_ids")`;
  - `ensure_book_collection() -> bool`, which returns `True` when it created the collection;
  - `delete_book_edition(book_id: str, edition_id: str) -> None`;
  - `upsert_book_chunks(chunks: list[BookChunk], vectors: list[list[float]], batch_size: int = 64) -> None`;
  - `count_book_points(book_id: str, edition_id: str) -> int`.

- [ ] **Step 1: Add the fixtures.**
  - `qdrant_url`: a `testcontainers.core.container.DockerContainer` running `docker-compose.yml`'s `services.qdrant.image` with port 6333 exposed. Poll `GET /readyz` for up to 30 s, then yield `http://<host>:<port>`.
  - `book_qdrant(qdrant_url, monkeypatch)`: sets `QDRANT_URL` and `BOOK_COLLECTION=test_books_<8 hex>`, clears `get_settings`' cache, calls `reset_book_client()`, and drops the collection afterwards.

- [ ] **Step 2: Write the failing tests:**

```python
def test_collection_shape(book_qdrant):   # ensure_book_collection() is True, then False; dense: size 768, cosine;
                                          # sparse "bm25" with modifier IDF; every BOOK_PAYLOAD_INDEXES field indexed
def test_upsert_count_and_reupsert(book_qdrant):   # 3 chunks -> count 3; same 3 again -> 3 (no duplicates)
def test_delete_edition(book_qdrant):     # chunks of two editions; delete one -> other's count unchanged
def test_server_side_bm25(book_qdrant):   # query_points(using="bm25", query=Document(text="phonorecords", model="qdrant/bm25"))
                                          # returns the one chunk whose body holds that word
```

Fake dense vectors: `[1.0] + [0.0] * 767`, with the 1.0 rotated per chunk.

- [ ] **Step 3: Run, expect failures.** Run: `uv run pytest tests/test_book_store.py -q`

- [ ] **Step 4: Implement.** Each point is a `PointStruct(id=chunk.point_id, vector={"dense": vec, "bm25": models.Document(text=chunk.body, model="qdrant/bm25")}, payload=chunk.model_dump())`.

- [ ] **Step 5: Run, expect pass, then commit** — `feat(rag): legal_books collection — dense + server-side BM25, no client_id`

---

### Task 10: Book search, lookup and node lookup (D6, D7)

**Files:**
- Create: `rag/book_search.py`
- Test: `tests/test_book_search.py`

**Interfaces:**
- Consumes: Task 9; `embed_query` (Task 1); `rerank` (`rag/reranker.py`); `parse_reference`, `split_ref_id` (Task 3); `BOOKS` (Task 2).
- Produces:
  - `@dataclass BookHit(chunk: BookChunk, score: float)`;
  - `kind_filter(include_history_notes: bool) -> models.Filter`;
  - `search_book_chunks(query: str, *, include_history_notes: bool = False) -> list[BookHit]`;
  - `@dataclass LookupResult(target: str, shown: list[BookChunk], more: list[str])`;
  - `lookup_book_chunks(reference: str) -> LookupResult | None`, where `None` means the reference couldn't be parsed and an empty `shown` means nothing matched;
  - `@dataclass NodeInfo(ref_id: str, label: str, kind: str, source_type: str, appendix: str | None, page_start: int, page_end: int, containers: set[str])`;
  - `fetch_nodes(ref_ids: Collection[str]) -> dict[str, NodeInfo]`.

- [ ] **Step 1: Write the failing tests.**
  - Use `book_qdrant` and seed one hand-built chunk of each kind: a rule, a definition, a history note, a treaty, a repealed stub, a three-part split node and a chunk with `sub_refs`.
  - Patch `rag.book_search.embed_query` to a deterministic hash vector, and set `RERANKER_ENABLED=false`.

```python
def test_default_search_excludes_notes_and_repealed():
def test_history_notes_on_request():          # include_history_notes=True returns the note; repealed still never
def test_treaty_in_default_results():         # the treaty hit has source_type "treaty"
def test_top_k_and_min_score(monkeypatch):    # BOOK_SEARCH_TOP_K=2 -> 2 hits; BOOK_MIN_SCORE above every score -> []
def test_lookup_section_in_order():           # lookup "§ 107" -> shown in payload order
def test_lookup_cap_lists_the_rest(monkeypatch):   # BOOK_LOOKUP_MAX_TOKENS below the split node's total ->
                                                   # shown = leading parts that fit (at least one), more = the remaining ref_ids
def test_lookup_reaches_repealed():
def test_lookup_sub_path_returns_its_chunk():
def test_lookup_unparseable_is_none():        # "tell me about fair use" -> None
def test_fetch_nodes():
    # split node: page span across its parts; containers == {its id}
    # sub-path: its SubRef label and pages; containers == {the containing chunk's ref_id}
    # unknown id absent
def test_search_never_reaches_client_collections():   # with a "legal_docs" collection also present, every
                                                      # query_points call names the book collection only (spy on the client)
```

- [ ] **Step 2: Run, expect failures.** Run: `uv run pytest tests/test_book_search.py -q`

- [ ] **Step 3: Implement.**
  - **Search:** one `query_points` call with two `models.Prefetch`es under the same `kind_filter`: dense, `limit=book_dense_candidates`, `using="dense"`; and `models.Document(text=query, model="qdrant/bm25")`, `limit=book_sparse_candidates`, `using="bm25"`. Fuse with `query=models.FusionQuery(fusion=models.Fusion.RRF)`. Then `rerank(query, results, top_n=book_search_top_k)`, then cut to `[:book_search_top_k]` (`rerank` returns everything when it is disabled), then apply `book_min_score` (ruling 14).
  - **Lookup:** `parse_reference`. A path is prefixed with every registered book's `ref_prefix`. Scroll with `should=[ancestor_ref_ids MatchAny, sub_ref_ids MatchAny]`, sort by `order`, and accumulate `tokens` until the next chunk would pass `book_lookup_max_tokens`, always showing at least one.

- [ ] **Step 4: Run, expect pass, then commit** — `feat(rag): book search (Qdrant RRF + reranker) and lookup by citation`

---

### Task 11: The tools, the research agent and the retrieval state

**Files:**
- Create: `rag/tools/search_books.py`, `rag/tools/lookup_book.py`
- Modify:
  - `observability/spans.py`: add `set_span_attributes`;
  - `observability/degradations.py`: add `BOOK_SEARCH_FAILED`;
  - `graph/state.py`;
  - `api/routes/query.py`: seed the new fields in `initial_state`;
  - `skills/legal_research/legal_research.py`, `skills/legal_research/prompts.py`;
  - `docs/architecture.md`: the state block.
- Test: `tests/test_book_tools.py`, `tests/test_state.py`, `tests/test_skills.py`

**Interfaces:**
- Consumes: Task 10; `REF_MARKER_RE` (Task 3).
- Produces:
  - `BOOK_TOOL_NAMES = frozenset({"search_books", "lookup_book"})`;
  - `format_chunks(chunks: list[BookChunk], start: int = 1) -> str`;
  - `retrieved_book_refs(messages: Iterable) -> set[str]`;
  - `@tool search_books(query: str, include_history_notes: bool = False) -> str` and `@tool lookup_book(reference: str) -> str`;
  - `set_span_attributes(**attrs: Any) -> None`, best-effort on the current span, never raising;
  - `BOOK_SEARCH_FAILED = "book_search_failed"` (SILENT);
  - `BOOK_REFS_CAP = 500` and `_book_refs_reducer(old: list[str] | None, new: list[str] | None) -> list[str]`;
  - in `LegalAgentState`: `book_refs_retrieved: Annotated[list[str], _book_refs_reducer]` (cumulative) and `book_refs_turn: list[str]` (this turn, sorted);
  - `_run_kb_research(state) -> tuple[str, list[dict], set[str], set[str]]`: content, edits, doc ids and book refs.

- [ ] **Step 1: Write the failing tests:**

```python
# test_book_tools.py
def test_output_format(chunk_107):   # chunk_107.body == "Notwithstanding …\nthe fair use of a copyrighted work …"
    assert format_chunks([chunk_107]) == (
        "[1] ref_id: circ92-2025-12:usc17-s107\n"
        "    17 U.S.C. §107 · rule · usc_title\n"
        "    Limitations on exclusive rights: Fair use\n"
        "    Notwithstanding …\n"
        "    the fair use of a copyrighted work …\n"
        "    history notes: circ92-2025-12:ch1-n40")
    # an effective-date note adds "    effective-date note: <id>" (ids only, comma-separated)
def test_output_never_prints_page_or_edition(any_chunks):   # find_stated_pages(out) == [] and "Dec. 2025" not in out
def test_lookup_lists_the_rest_without_marker():  # "more in this node (not shown; look them up by id): <id>, <id>"
                                                  # and retrieved_book_refs() of that ToolMessage excludes those ids
def test_retrieved_refs_only_from_book_tool_messages():
    # ToolMessage(name="search_books", content="[1] ref_id: X ...") -> {X}
    # AIMessage("ref_id: Y") and ToolMessage(name="search_legal", content="ref_id: Z") -> not counted
def test_search_failure_degrades(monkeypatch):    # search_book_chunks raises ConnectionError ->
    # tool returns "Book search is unavailable right now (ConnectionError). Answer without the book and say so."
    # record_degradation called with (BOOK_SEARCH_FAILED, announced=False)
def test_tool_spans_carry_ref_ids(span_exporter): # span "tool.search_books" has attribute "book.ref_ids"
# test_state.py
def test_book_refs_reducer():   # idempotent when new == old; [a,b] + [b,c] -> [a,b,c]; [a,b] + [a] -> [b,a];
                                # 600 distinct -> last 500
# test_skills.py (legal_research reached via importlib.import_module)
def test_book_tools_only_in_research_agent():  # _build_agent passes [search_legal, get_document, escalate,
                                               # search_books, lookup_book]; contract_generation's agent tools contain neither
def test_research_agent_pins_num_ctx():        # ChatOllama built with num_ctx == settings.ollama_num_ctx
def test_kb_research_sets_book_refs():         # fake agent returns a search_books ToolMessage -> state["book_refs_turn"] == [X],
                                               # X in state["book_refs_retrieved"]
def test_doc_chat_clears_book_refs_turn():     # book_refs_turn == []
def test_research_prompt_rules():              # RESEARCH_SYSTEM_PROMPT contains "ref_id:", "page", "edition",
                                               # "amendment history", "not U.S. law", and its client_id rule names client collections
```

- [ ] **Step 2: Run, expect failures.** Run: `uv run pytest tests/test_book_tools.py tests/test_state.py tests/test_skills.py -q`

- [ ] **Step 3: Implement.**
  - **The tools** are thin `@tool` wrappers over `@traced("tool.search_books")` and `@traced("tool.lookup_book")` functions. Each docstring says what the book is ("Circular 92: Title 17 U.S.C. (copyright) and related laws, Dec. 2025 edition") and that output ids are cited as `ref_id: <id>`.
  - **For a history note**, line 2 is `<label> · history_note · <source_type>` and line 3 is the chunk `heading`.
  - **In `RESEARCH_SYSTEM_PROMPT`**, add the book to PROCESS: questions on U.S. copyright law go to `search_books`, or `lookup_book` for a known citation. Add three RULES lines, principles only, with no worked example:
    - cite book passages as `ref_id: <id>`, exactly as the tool printed it;
    - never write a page or an edition: they are added for you;
    - a history note is amendment history, not statutory text, and a treaty is not U.S. law.

    Change "Always filter by client_id" to "Always filter by client_id when searching client documents (search_legal, get_document)".
  - **State fields:** seed `"book_refs_retrieved": []` and `"book_refs_turn": []` in `initial_state`, and add both to `docs/architecture.md`'s state block.

- [ ] **Step 4: Run tests, the vocabulary gate and the wiki check**

Run: `uv run pytest tests/ -q && uv run python scripts/check_degradation_vocabulary.py && uv run python scripts/check_wiki.py`
Expected: all pass.

- [ ] **Step 5: Commit** — `feat(research): search_books and lookup_book in the research agent; book refs kept per conversation`

---

### Task 12: The citation check, rendering and audit (D8, D9)

**Files:**
- Create: `rag/book_citations.py`
- Modify: `graph/nodes/risk_assessor.py`, `graph/nodes/output_formatter.py`, `graph/nodes/memory_writer.py`, `memory/audit.py`, `memory/db.py`, `graph/state.py`, `api/routes/query.py`, `docs/architecture.md`
- Test: `tests/test_book_citation_check.py`, `tests/test_memory_writer.py`, `tests/test_audit.py`

**Interfaces:**
- Consumes: `fetch_nodes` (Task 10); `find_cited_ref_ids`, `render_citation`, `render_response`, `find_stated_pages`, `book_for_ref` (Tasks 2–3); `book_refs_retrieved` and `book_refs_turn` (Task 11).
- Produces:
  - `check_book_citations(text: str, retrieved: Collection[str]) -> list[dict]`, one `{"ref_id", "valid": bool, "citation": str | None, "reason": str | None}` per cited id. It returns `[]` without calling Qdrant when no id is cited.
  - `_check_citations(llm_response, chunks, has_uploaded_doc=False, *, book_citations: Sequence[dict] = (), book_retrieved: bool = False) -> list[dict]`.
  - The state field `book_citations: list[dict]`, per turn and seeded `[]`.
  - `write_audit_log(..., sources: dict | None = None)`.
  - The column `audit_log.sources JSONB NOT NULL DEFAULT '{}'`.

- [ ] **Step 1: Write the failing tests.** `fetch_nodes` is patched with a fake for the risk and format tests.

```python
def test_book_only_turn_with_valid_citations_passes():   # chunks [], book_refs_retrieved [X], answer cites X -> risk "low"
def test_earlier_turn_ref_passes():          # X in book_refs_retrieved, book_refs_turn [] -> no flag
def test_never_retrieved_id_is_flagged():    # flag {"reason": "Cites a reference that was not retrieved: <id>", "severity": "high"}
def test_sub_path_under_retrieved_chunk():   # cited Y with fetch_nodes -> containers {X}, X retrieved -> valid, rendered with Y's page
def test_sub_path_under_unretrieved_chunk(): # containers {Z}, Z not retrieved -> flagged
def test_nonexistent_id_is_flagged():        # fetch_nodes has no entry -> flagged (other-edition id included)
def test_qdrant_failure_flags_every_cited_id():   # fetch_nodes raises -> "Could not verify a cited reference: <id>" for each;
                                                  # rendered "[unverified reference: <id>]"
def test_model_page_flagged_when_book_used():     # cites X validly + "see p. 12" -> flag "The answer states a page itself"
def test_client_doc_answer_with_page_not_flagged():   # doc chunks only, no book refs, "p. 3 of the MSA" -> no page flag
def test_rendering():
    # report["response"] has the rendered citation; llm_response unchanged (raw id);
    # invalid -> "[unverified reference: <id>]"; report["sources"] gains {"ref_id", "citation"} for each valid one
def test_doc_only_paths_unchanged():          # the existing _check_citations tests still pass unedited
# test_memory_writer.py
def test_audit_sources():   # write_audit_log called with sources == {"doc_ids": [...], "ref_ids_retrieved": book_refs_turn,
                            #                                         "ref_ids_cited": [every cited id, valid or not]}
# test_audit.py
def test_sources_column():  # a row written with sources reads back as the dict; a row without -> {}
```

- [ ] **Step 2: Run, expect failures.** Run: `uv run pytest tests/test_book_citation_check.py tests/test_memory_writer.py tests/test_audit.py -q`

- [ ] **Step 3: Implement.**
  - **Validity:** a cited id is valid when it is in `retrieved`, or when `fetch_nodes` returns it with `containers ∩ retrieved` non-empty. The citation is rendered from the `NodeInfo` and `book_for_ref(id)`.
  - **`risk_assessor`, non-review branch:**
    - `book_citations = check_book_citations(llm_response, state.get("book_refs_retrieved") or [])`, stored in state;
    - `_check_citations(..., book_citations=..., book_retrieved=bool(state.get("book_refs_retrieved")))`;
    - the page flag when `book_citations` or `state.get("book_refs_turn")` is non-empty and `find_stated_pages(llm_response)` is non-empty.
  - **`_check_citations`:**
    - The answer counts as cited when it names a retrieved doc as today, or has a valid book citation.
    - "No citation possible — no chunks retrieved" fires only when there are no chunks and `book_retrieved` is false.
    - The existing flag strings are unchanged.
  - **`output_formatter`:** `response = render_response(llm_response, {valid ids: citation})` when `book_citations` is non-empty.
  - **Audit:** `memory/db.py` adds `"ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS sources JSONB NOT NULL DEFAULT '{}'"`, and `write_audit_log` passes `psycopg.types.json.Jsonb(sources or {})`.
  - **State:** seed `"book_citations": []` in `initial_state` and list it in `docs/architecture.md`.

- [ ] **Step 4: Run** `uv run pytest tests/ -q && uv run python scripts/check_wiki.py`. Expected: pass.

- [ ] **Step 5: Commit** — `feat(research): check, render and audit book citations by ref_id`

---

### Task 13: The ingest command and the dry-run report (D10, D11 bar)

**Files:**
- Create: `scripts/ingest_book.py`, `ingest/books/report.py`
- Modify: `ingest/books/__init__.py`, adding `parse_book(book: BookEntry, pdf_path: Path) -> ParseResult`, which dispatches on `book.front_end`
- Test: `tests/test_ingest_book.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `build_report(parsed: ParseResult, chunks: list[BookChunk], *, budget: int) -> dict`;
  - `BAR_EXPECTED = {"title17_sections": 152, "markers": 192, "appendices": 20}`;
  - `DEPLOY_LOGS_DIR = Path("data/deploy-logs")`;
  - `main(argv: list[str] | None = None) -> int`. Exit codes: 0 is OK; 1 means the bar failed or the ingest check failed; 2 means refused (unregistered book or hash mismatch).

- [ ] **Step 1: Write the failing tests:**

```python
def test_unregistered_book_refused(capsys):    # main(["nope", pdf]) == 2, message names the id
def test_hash_mismatch_refused(tmp_path):      # main(["circ92", other_pdf]) == 2
def test_report_bar(fake_parse):
    # every bar item present: title17_sections, markers (total, linked, scope_mismatches), appendices {L: source_type},
    # out_of_sequence, xref resolution {total, resolved, rate, misses}, chunks_by_kind, largest_chunk_tokens,
    # over_budget, overrides_unused, hyphens_undecided, bar {name: bool}, bar_passed
def test_dry_run_writes_files(tmp_path, monkeypatch, fake_parse):
    # DEPLOY_LOGS_DIR -> tmp; files <book>-<edition>-<UTC stamp>-{chunks.jsonl, chunks.tsv, report.json, sample20.tsv};
    # no Qdrant call; exit 0 when the bar passes, 1 when not (files still written)
def test_ingest_refuses_when_bar_fails(fake_parse_failing): # exit 1, nothing upserted
def test_ingest_and_reingest(book_qdrant, fake_parse, fake_embed):  # exit 0, count == len(chunks); run again -> same count
```

`fake_parse` monkeypatches `scripts.ingest_book.parse_book` and the counter with a small hand-built `ParseResult`; registry verification uses the real fixture PDF.

- [ ] **Step 2: Run, expect failures.** Run: `uv run pytest tests/test_ingest_book.py -q`

- [ ] **Step 3: Implement**, following D10's steps in order.
  - **The bar** passes when all of these hold:
    - `title17_sections == 152`;
    - markers total `== 192`, all linked, and no scope mismatch;
    - 20 appendices, each with its `source_type`;
    - `out_of_sequence == []`;
    - `over_budget == []`;
    - `overrides_unused == []`.

    The xref rate and the misses are reported, not gated.
  - **The 20-chunk sample:** `random.Random(seed)`, with the seed printed and stored in the report.
  - **The TSV columns:** `ref_id, chunk_key, label, pages, kind, source_type, tokens, xrefs, citation`.
  - **Ingest:**
    - `ensure_book_collection()`, then `delete_book_edition()`;
    - `embed_texts([c.text for c in batch])` in batches of 32;
    - `upsert_book_chunks`;
    - `count_book_points == len(chunks)`, or exit 1.
  - **The log line:** book, edition, chunks by kind, largest chunk in tokens, seconds.

- [ ] **Step 4: Run, expect pass, then commit** — `feat(books): admin ingest command with dry run and whole-book bar`

---

### Task 14: The retrieval eval runner

**Files:**
- Create: `evals/books/__init__.py`, `evals/books/run_retrieval.py`, `evals/books/questions.example.txt`
- Test: `tests/test_book_retrieval_eval.py`

**Interfaces:**
- Consumes: `search_book_chunks`, `lookup_book_chunks` (Task 10); `is_under` (Task 3).
- Produces:
  - `@dataclass Question(text: str, gold: list[str], type: str)`, where `type` is `search`, `citation` or `none`;
  - `load_questions(path: Path) -> list[Question]`, reading `question | gold ids (comma-separated) | type` and skipping `#` and blank lines;
  - `is_hit(ref_id: str, sub_ref_ids: Sequence[str], gold: str) -> bool`;
  - `score_ranked(ranked: list[tuple[str, list[str]]], gold: list[str]) -> dict`, with `recall@5`, `recall@10`, `rr` and `ndcg@10`;
  - `run(questions, search=search_book_chunks, lookup=lookup_book_chunks) -> dict`;
  - `main(argv) -> int`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_load_questions(tmp_path):      # three lines + a comment -> 3 Questions; gold split on commas, stripped
def test_is_hit():                      # equal; returned under gold (usc17-s107-1 for gold usc17-s107); gold in sub_ref_ids; else False
def test_metrics():                     # ranked [miss, hit, miss], one gold -> recall@5 1.0, rr 0.5, ndcg@10 == 1/log2(3)
def test_run_with_stubs():              # search type uses search; citation type is a hit when a gold id is in lookup.shown;
                                        # none type counts toward not_found_rate when search returns []
```

- [ ] **Step 2: Run, expect failures**, then implement. The example file holds three lines (one per type), commented as format-only; it is not a test set.

- [ ] **Step 3: Run, expect pass, then commit** — `feat(evals): retrieval runner for the book test set (Recall@5/10, MRR, nDCG@10, not-found)`

---

### Task 15: Local integration (with the owner)

No code unless a defect is found. A defect gets a failing test first, in the task that owns the code. Local Ollama (embeddinggemma and `LLM_MODEL`) and local Qdrant (`docker compose up -d qdrant`) must be running.

- [ ] **Step 1: Run the dry run.**

Run: `uv run python -m scripts.ingest_book circ92 tests/fixtures/books/circular-92-2025-12.pdf --dry-run`
Expected: exit 0 and `bar_passed: true`. Chunks by kind for ch.1 + ch.5 should be close to the prototype's 436 / 54 / 77 / 1 (rule / definition / history note / repealed). Any difference must be explained by rulings 8–9. The largest chunk must be 800 tokens or under.

- [ ] **Step 2: Stop for the owner.**
  - Send them the `report.json` summary and the path of the `sample20.tsv`.
  - **A person checks the 20 chunks against the printed page, text and page number, before the first ingest** (spec D11).
  - **The owner also reviews the 17 effective-date notes** from Task 7's report.
  - Continue only on their OK.

- [ ] **Step 3: Ingest locally.** Run the same command without `--dry-run`. Expected: exit 0, and the point count equals the chunk count.

- [ ] **Step 4: Ask the five questions** through `POST /api/query` (`task_type: "research"`, no document), with the backend started by `bash scripts/start.sh`:
  1. "What does §107 say?"
  2. "What is a joint work?"
  3. "When did paragraph (5) of the definition of 'international agreement' take effect?"
  4. "How long does the TRIPs Agreement protect layout-designs of integrated circuits?"
  5. "What does Ukrainian law say about software copyright?"

  For each turn, record:
  - the tools called;
  - the rendered citations, checked against the printed pages;
  - `risk_flags`;
  - the `audit_log.sources` row (`SELECT sources FROM audit_log ORDER BY id DESC LIMIT 1` on app-db).

  Expected:
  - Q1 uses `lookup_book`.
  - Q3 fetches the effective-date note through its flag.
  - Q4 renders "(treaty, not U.S. law)".
  - Q5 doesn't cite the book as an answer.
  - No unverified reference appears unflagged.

- [ ] **Step 5: Write the results** into the task report, for the shipped-log row in Task 16.

---

### Task 16: Docs and the record

**Files:**
- Modify:
  - `docs/rag.md`: the collection, ingest, book search, the settings and D3;
  - `docs/architecture.md`: the tools and the audit column (the state fields were done in Tasks 11–12);
  - `docs/operations.md`: the seven settings, `python -m scripts.ingest_book`, the PDF at `data/books/` on the VM, and the audit column;
  - `docs/observability.md`: the two codes;
  - `docs/context_and_memory_audit.md`: the research prompt's book rules and `book_refs_retrieved` in the checkpointer;
  - `docs/shipped-log.md`: a top row, with Task 15's results;
  - `docs/wiki.md`: the date;
  - `docs/roadmap.md`: rewrite the *Index the legal reference books* row to what stays open. That is the test set, the budget and the embedding model, `BOOK_MIN_SCORE`, short chunks, history questions, precedence and access, and the VM rollout (Spark BM25 probe, deploy, ingest);
  - `docs/superpowers/specs/README.md` and the spec's status line;
  - `CLAUDE.md` rule 4: drop "(`ref_id:` arrives with the reference-books build)" and say the parser reads `ref_id:` from the book tools.
- Delete: this plan ([decision](../../decisions/2026-10-28-plans-deleted-once-shipped.md)), and its links from the spec and the specs README.

- [ ] **Step 1: Make the edits and delete the plan.**
- [ ] **Step 2: Run the full gate.**

Run: `bash scripts/check.sh`
Expected: `==> all checks passed`. The image build must still import `api.main`: `ingest/books/` and `rag/book_*` are imported from the app and need only pdfplumber and qdrant-client, which are already in the runtime lock.

- [ ] **Step 3: Commit** — `docs: reference books — record, settings, ingest command, codes`

---

## Rollout (not part of the build; each step needs the owner's OK when it is due)

1. **Spark's Qdrant 1.17.1: check server-side BM25** with a throwaway collection, created and then deleted. It must leave compliance-bot's collections alone. If it fails, the spec's fallback applies: fastembed's `Qdrant/bm25` computed in our code, a dependency in both requirement sets, and its model files baked into the image with offline mode forced. That changes Tasks 9 and 10.
2. **VM deploy:** copy the PDF to `data/books/` and mount it into the backend, then deploy. CLAUDE.md rule 6: ask whether the owner runs it or I do.
3. **VM ingest:** run `scripts.ingest_book` against Spark's Qdrant, then put the five questions to the API.
