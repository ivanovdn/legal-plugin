# Book Retrieval Test Set Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the four question files that `evals.books.run_retrieval` scores, add the `history` question type, check the gold twice before any score exists, then record the first baseline.

**Architecture:**
- **The runner** gains one question type, `history`.
- **The set** is four plain-text files in the runner's existing format: `question | gold | type`, with `#` comments for provenance.
- **Checks:** a pytest module checks the files against the parsed book and against their own format rules.
- **Scratch work:** extracting the candidate questions from Law Stack Exchange and dumping the book for reading are scratch scripts, not repo code.

**Tech Stack:** Python 3.12, pytest, the repo's book parser (`ingest.books`), and pyarrow for the scratch extraction only, via `uv run --no-project --with pyarrow` (no new project dependency).

**Spec:** [docs/superpowers/specs/2026-11-18-book-retrieval-test-set-design.md](../specs/2026-11-18-book-retrieval-test-set-design.md). Read it before any task. Its anti-goals bind every task.

## Global Constraints

- **No scoring before Task 9.** Until then, nobody runs `evals.books.run_retrieval`, `search_book_chunks`, `lookup_book_chunks`, the research agent, or any query against the `legal_books` Qdrant collection. Gold comes from the book dump (Task 2) only.
- **Questions are copied word for word.** Use the Law Stack Exchange title, HTML-unescaped, with whitespace collapsed to single spaces. Add one sentence of the body, also word for word, only when the title isn't a question by itself. Never reword, and keep typos.
- **Gold is a bare node path** (`usc17-s201-a`), never a chunk key and never a `circ92-2025-12:` prefix. Use the smallest node that holds the whole answer. List every node a complete answer needs, because the runner requires all of them.
- **Dataset:** `ymoslem/Law-StackExchange`, revision `ab2dbaad9a71b6550316994c8fcd69f24f7f16e6`.
- **Seed:** `20261118`. Shuffle a pool sorted by `question_id` with `random.Random(20261118).shuffle(pool)`.
- **Link form:** `https://law.stackexchange.com/q/<question_id>`.
- **Each row's licence** comes from its own `license` field: `CC BY-SA 3.0` or `CC BY-SA 4.0`. The files are under CC BY-SA 4.0.
- **Scratch directory:** `SP=/private/tmp/claude-501/-Users-dmytroivanov-projects-legal-plugin/56ec1aaa-3540-40d0-9df3-3838e295be5c/scratchpad`. The parquet is already at `$SP/hf/lse/train.parquet`. Scripts go in `$SP/scripts/`, outputs in `$SP/testset/`. Run any Python that reads the dataset with `-I`.
- **Imports at the top of the file** (CLAUDE.md rule 1).
- **Commits:** every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Commits before Task 8 are work in progress. The freeze is Task 8's commit.
- **Don't touch the running app or the VM.**

## Review Focus

1. **A gold id that is grammatical but names no node.** The runner can't see this, so the question becomes a miss forever. Covered by `test_every_gold_names_a_node_of_the_book` (Task 3a).
2. **A citation question that doesn't parse to its own gold.** It would score the parser, not the lookup. Covered by `test_every_citation_question_names_its_gold` (Task 5).
3. **A `history` question searched without notes.** Its gold could never be found. Covered by `test_a_history_question_is_searched_with_history_notes_included` (Task 1). Likewise a note as the gold of a `search` question, and a `history` question with no note among its golds: covered by `test_a_note_is_gold_only_for_a_history_question` (Task 6).
4. **A searched question (`lse-search.txt` or `written.txt`) that names a section, or a question that appears twice.** Either inflates the score. Covered by `test_no_searched_question_names_a_section` (Tasks 3a and 6) and `test_no_question_appears_twice` (Task 3a).
5. **A Law Stack Exchange question without its link or licence**, which the licence forbids. Covered by `test_lse_questions_carry_their_source_and_licence` (Tasks 3a and 4).

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `evals/books/run_retrieval.py` | modify | the `history` type: parse, search with notes, score and report |
| `evals/books/questions.example.txt` | modify | add one `history` line; later, point at the real set |
| `tests/test_book_retrieval_eval.py` | modify | `history` tests; the stub's `search` takes `include_history_notes` |
| `evals/books/sets/lse-search.txt` | create | ~60 `search` questions from Law Stack Exchange |
| `evals/books/sets/lse-none.txt` | create | ~15 `none` questions from Law Stack Exchange |
| `evals/books/sets/citations.txt` | create | ~15 `citation` questions written from the book's structure |
| `evals/books/sets/written.txt` | create | ~15 `search`/`history` questions written by Claude |
| `tests/test_book_test_set.py` | create | the format and gold checks for the four files |
| `docs/rag.md`, `docs/operations.md`, `docs/architecture.md`, `docs/roadmap.md`, `docs/shipped-log.md`, `docs/wiki.md`, the spec, `docs/superpowers/specs/README.md` | modify | Task 9 |

### Line formats (used by Tasks 3–8)

**Header** of the two Law Stack Exchange files: `#` lines, which must contain:
- `ymoslem/Law-StackExchange`
- the revision
- `CC BY-SA 4.0`
- `questions copied verbatim; gold labels added`
- the pool rule, with the foreign-tag list and the pool size
- `random.Random(20261118)`
- the spec's path

The header of `written.txt` contains `written by Claude`.

**Provenance comments**, each on the line directly above its question:
```
lse-search:  # lse:<id> https://law.stackexchange.com/q/<id> · <licence> · cited: <answerers' 17 U.S.C. citations, or none> · <answers|governs> · gold: <one-line reason>
lse-none:    # lse:<id> https://law.stackexchange.com/q/<id> · <licence> · tags: <tags> · none: <why no node of the book answers it>
written:     # written by Claude · <search|history> · gold: <one-line reason>
dropped:     # dropped lse:<id> · <reason>
```
- **The `·`** is U+00B7, with one space each side.
- **Task 7** appends ` · second reading: agreed`, or ` · second reading: <their gold> — settled: <reason>`.
- **Task 8** appends ` · owner's check: <what changed, or confirmed>`.
- **`citations.txt`** needs no provenance line.

---

### Task 1: The `history` question type

**Files:**
- Modify: `evals/books/run_retrieval.py`
- Modify: `evals/books/questions.example.txt`
- Test: `tests/test_book_retrieval_eval.py`

**Interfaces:**
- Produces:
  - `QUESTION_TYPES == ("search", "citation", "none", "history")`
  - `run(questions, search: Callable[..., list[BookHit]] = search_book_chunks, lookup=...)` calls `search(text, include_history_notes=True)` for `history` questions, and `search(text)` for `search` and `none`, as today
  - result keys `history_recall@5`, `history_recall@10`, `history_mrr`, `history_ndcg@10`, and `counts["history"]`
  - history records carry `"type": "history"`
  - the report prints a `history` row

- [ ] **Step 1: Give the test stub the keyword and write the failing tests**

`_Stubs.search(self, query: str, *, include_history_notes: bool = False)` records the query in `self.searched` (as now) and, when the flag is true, also in a new `self.searched_with_notes: list[str]`. `CH1N13 = f"{PREFIX}:ch1-n13"`.

```python
def test_a_history_question_is_searched_with_history_notes_included():
    stubs = _Stubs(search={"when?": [_chunk("ch1-n13")], "fair use?": [_chunk("usc17-s107")]}, lookup={})
    run([Question("when?", [CH1N13], "history"), Question("fair use?", [S107], "search")],
        search=stubs.search, lookup=stubs.lookup)
    assert stubs.searched_with_notes == ["when?"]
    assert stubs.searched == ["when?", "fair use?"]

def test_history_is_scored_like_search_and_reported_apart():
    stubs = _Stubs(search={"when?": [_chunk("usc17-s108"), _chunk("ch1-n13")], "fair use?": []}, lookup={})
    result = run([Question("when?", [CH1N13], "history"), Question("fair use?", [S107], "search")],
                 search=stubs.search, lookup=stubs.lookup)
    assert result["counts"]["history"] == 1
    assert result["history_recall@5"] == 1.0 and result["history_mrr"] == 0.5
    assert result["recall@5"] == 0.0  # the search mean is over search questions only
    assert result["questions"][0]["type"] == "history"

def test_a_history_question_needs_a_gold(tmp_path):
    with pytest.raises(ValueError, match="needs at least one gold id"):
        load_questions(_write(tmp_path, "when? | | history\n"))

def test_main_prints_a_history_row(tmp_path, capsys, monkeypatch):
    # stub as in test_main_prints_a_per_type_table…; file "when? | {CH1N13} | history"; search {"when?": [_chunk("ch1-n13")]}
    rows = ...  # same parsing as that test
    assert rows["history"][1] == "1" and rows["history"][2] == "1.000"
    assert rows["history"][6] == "-" and rows["history"][7] == "-"  # hit_rate, not_found
```

Update the existing assertions: `counts` gains `"history": 0` in `test_run_with_stubs`, and wherever else a test compares the whole `counts` dict or the result's keys. `test_the_example_file_loads_and_has_one_of_each_type` expects `["citation", "history", "none", "search"]`.

- [ ] **Step 2: Run them, expect failures**

Run: `uv run pytest tests/test_book_retrieval_eval.py -q`
Expected: the four new tests fail, `type 'history' is not one of …`, and the example-file test fails.

- [ ] **Step 3: Implement**

In `run_retrieval.py`:
- Add `"history"` to `QUESTION_TYPES`.
- Give `_search_record` a `kind: str` parameter, which becomes its record's `"type"`.
- Dispatch `history` in `run`.
- Add the four `history_*` means, each over the history records.
- Restructure `_COLUMNS` so each column maps a question type to its result key. `recall@5` reads `recall@5` for search and `history_recall@5` for history, and so on. `format_report` fills the history row from it.
- Update the module docstring's type list: `history` searches with history notes included and is scored like `search`.

In `questions.example.txt`, add:

```
When did paragraph (5) of the "international agreement" definition take effect? | circ92-2025-12:ch1-n13 | history
```

Also add a line to its header explaining `history`.

- [ ] **Step 4: Run the tests, expect a pass**

Run: `uv run pytest tests/test_book_retrieval_eval.py -q`
Expected: all pass.

- [ ] **Step 5: Mutation check**

1. Temporarily make `history` call `search(question.text)` without the keyword.
2. Confirm that `test_a_history_question_is_searched_with_history_notes_included` fails.
3. Revert.

- [ ] **Step 6: Commit**

```bash
git add evals/books/run_retrieval.py evals/books/questions.example.txt tests/test_book_retrieval_eval.py
git commit -m "feat(evals): a history question type that searches with history notes included"
```

---

### Task 2: Scratch inputs: the book dump and the two candidate pools (no commit)

**Files (scratch only):**
- Create: `$SP/scripts/book_dump.py`
- Create: `$SP/scripts/lse_pool.py`
- Outputs: `$SP/testset/book.jsonl`, `book-index.tsv`, `pool-search.jsonl`, `pool-none.jsonl` and `pool-meta.json`

**Interfaces:**
- Produces:
  - **`book.jsonl`:** one row per node, in document order, with `path`, `kind`, `label`, `heading`, `pages` `[first, last]`, `text` (`tree.join(node.all_lines())`, everything under the node) and `own` (`own_text(node, tree.join)`).
  - **`book-index.tsv`:** `path`, `kind`, `label`, `heading` and `pages`, one line per node.
  - **`pool-*.jsonl`:** one row per candidate, in shuffled order, with:
    - `rank`, `id`, `link`, `licence`, `score` and `tags`
    - `title` (unescaped)
    - `body` (HTML stripped, whitespace collapsed)
    - `answers`: `[{score, text, cited: [17 U.S.C. citations found]}]`, best score first
  - **`pool-meta.json`:** the revision, the seed, the foreign-tag list, each pool's rule and each pool's size.

- [ ] **Step 1: Dump the book**

`book_dump.py out_dir` does three things:
1. Builds the tree with `parse_book(get_book("circ92"), Path("tests/fixtures/books/circular-92-2025-12.pdf")).tree`.
2. Walks it with `tree.walk()`.
3. Writes the two files.

Run: `PYTHONPATH=. uv run python $SP/scripts/book_dump.py $SP/testset`
Expected: the row count in `book.jsonl` equals `len(tree.index())`, and the paths `usc17-s107`, `usc17-s101-def-joint-work`, `ch1-n13` and `trips-art35` are all present.

- [ ] **Step 2: Extract the pools**

`lse_pool.py parquet out_dir` reads the parquet with pyarrow.

The search pool takes a question only if all of these hold:
- one of its tags is exactly `copyright`, `fair-use`, `dmca` or `public-domain`;
- none of its tags is in FOREIGN, the list of country and region tags other than `united-states` (build the list from the dataset's tag counts and write it to `pool-meta.json`);
- some answer matches `17\s*U\.?\s*S\.?\s*C\.?` (case-insensitive);
- `score >= 2`.

The none pool takes a question only if all of these hold:
- one of its tags is `trademark` or `patents`;
- none of the four copyright tags above;
- no FOREIGN tag;
- `score >= 2`.

Sort each pool by `question_id`, then shuffle it with `random.Random(20261118)`.

Run: `uv run --no-project --with pyarrow python -I $SP/scripts/lse_pool.py $SP/hf/lse/train.parquet $SP/testset`
Expected:
- the search pool holds about 200 rows (the builder check counted about 204) and the none pool more than 100;
- `pool-meta.json` records both sizes;
- both licence values appear.

Check that the parquet is the pinned revision: `curl -s https://huggingface.co/api/datasets/ymoslem/Law-StackExchange` still reports `sha` `ab2dbaad9a71b6550316994c8fcd69f24f7f16e6`. If it doesn't, stop and report.

---

### Task 3a: `lse-search.txt`, the checks, and the first 20 questions

**Files:**
- Create: `tests/test_book_test_set.py`
- Create: `evals/books/sets/lse-search.txt`

**Interfaces:**
- Consumes: `load_questions` and `QUESTION_TYPES` from `evals.books.run_retrieval`; the `circ92_whole_parse` session fixture (`tests/conftest.py`); `parse_reference` from `ingest.books.ref_ids`; the Task 2 outputs.
- Produces: `SETS = Path("evals/books/sets")`, a dict `SET_FILES = {"lse-search.txt": (("search",), 55, 65)}` mapping each file to its allowed types and min/max count (later tasks add their entries), and a helper `_entries(path) -> list[tuple[str, str]]` that returns `(comment_line_directly_above, question_line)` for every question line.

- [ ] **Step 1: Write the checks (they fail: the file doesn't exist)**

```python
LSE_SEARCH_RE = re.compile(r"^# lse:(\d+) https://law\.stackexchange\.com/q/\1 · CC BY-SA (?:3\.0|4\.0) · cited: .+ · (?:answers|governs) · gold: .+$")
SECTION_RE = re.compile(r"§|\bU\.?\s?S\.?\s?C\b|\bsection\s+\d|\b\d{3}[A-C]?\([a-z0-9]+\)", re.I)

@pytest.mark.parametrize("name", SET_FILES)
def test_each_set_file_has_its_types_and_size(name):  # every type in the file's allowed types; min <= count <= max

@pytest.mark.parametrize("name", SET_FILES)
def test_every_gold_names_a_node_of_the_book(name, circ92_whole_parse):
    index = circ92_whole_parse.tree.index()
    missing = [(q.text, g) for q in load_questions(SETS / name) for g in q.gold if g.rpartition(":")[2] not in index]
    assert missing == []

def test_no_question_appears_twice():  # across every file in SET_FILES, question texts are unique

def test_lse_questions_carry_their_source_and_licence():  # every lse-search entry's comment matches LSE_SEARCH_RE; ids unique

def test_no_lse_search_question_names_a_section():  # SECTION_RE finds nothing in any lse-search question

def test_lse_headers_name_the_source_revision_and_licence():
    # header contains: "ymoslem/Law-StackExchange", "ab2dbaad9a71b6550316994c8fcd69f24f7f16e6", "CC BY-SA 4.0",
    # "questions copied verbatim; gold labels added", "random.Random(20261118)"
```

Run: `uv run pytest tests/test_book_test_set.py -q`
Expected: fails with FileNotFoundError.

- [ ] **Step 2: Write the header, then walk `pool-search.jsonl` from rank 1**

For each candidate, read the title, the body and the answers. The answers' citations are hints only. Then take the question or drop it (spec, *Choosing the `search` questions*):

- **Drop it** if no provision governs it, if its text names a section, if it repeats a question already taken, or if it holds no question. Write the `dropped` line with the reason.
- **Take it otherwise.** Find the gold in `book.jsonl`/`book-index.tsv`, using the smallest node and every node the answer needs. Mark it `answers` or `governs`. Write the provenance line and the question line, with `search` as the type.

Stop after the 20th question taken. The header records the last rank walked.

- [ ] **Step 3: Run the checks**

Run: `uv run pytest tests/test_book_test_set.py -q`
Expected: everything passes except the size check (20 is below 55). Mark that test `xfail(strict=True, reason="filled to 55+ in Task 3c")`. Task 3c removes the mark.

- [ ] **Step 4: Commit**

```bash
git add tests/test_book_test_set.py evals/books/sets/lse-search.txt
git commit -m "test(books): test set checks; lse-search questions 1-20"
```

### Task 3b: `lse-search.txt`, questions 21–40

**Files:** Modify: `evals/books/sets/lse-search.txt`
**Interfaces:** consumes the formats and rules of Task 3a.

- [ ] **Step 1:** Continue the walk from the rank after the last one recorded in the header, and stop after the 40th question taken. Update the last rank walked.
- [ ] **Step 2:** Run `uv run pytest tests/test_book_test_set.py -q`. Expected: pass, with the size check still xfail.
- [ ] **Step 3:** Commit: `test(books): lse-search questions 21-40`

### Task 3c: `lse-search.txt`, questions 41–60

**Files:** Modify: `evals/books/sets/lse-search.txt` and `tests/test_book_test_set.py`

- [ ] **Step 1:** Continue the walk to the 60th question taken. Update the header: the last rank walked, the number taken, the number dropped, and the counts of `answers` and `governs`.
- [ ] **Step 2:** Remove the size check's `xfail`. Run `uv run pytest tests/test_book_test_set.py -q`. Expected: all pass.
- [ ] **Step 3:** Commit: `test(books): lse-search questions 41-60`

---

### Task 4: `lse-none.txt`

**Files:** Create `evals/books/sets/lse-none.txt`; modify `tests/test_book_test_set.py`

- [ ] **Step 1: Extend the checks**

- Add `"lse-none.txt": (("none",), 12, 18)` to `SET_FILES`.
- Add `LSE_NONE_RE = re.compile(r"^# lse:(\d+) https://law\.stackexchange\.com/q/\1 · CC BY-SA (?:3\.0|4\.0) · tags: .+ · none: .+$")`.
- Extend `test_lse_questions_carry_their_source_and_licence` and `test_lse_headers_name_the_source_revision_and_licence` to this file. Ids must be unique across both Law Stack Exchange files.

Run the checks. Expected: fails, because the file is missing.

- [ ] **Step 2: Walk `pool-none.jsonl` from rank 1, taking until 15**

Take a question only when no node of the book answers it. Check Title 17, the Titles 18, 28 and 44 sections in the book, the Public Laws and TRIPs arts. 35–38. For example, a trademark-jurisdiction question is answered by `usc28-s1338` and is dropped. Write the `none:` reason, and a `dropped` line for each drop.

- [ ] **Step 3:** Run `uv run pytest tests/test_book_test_set.py -q`. Expected: pass.
- [ ] **Step 4:** Commit: `test(books): lse-none questions`

---

### Task 5: `citations.txt`

**Files:** Create `evals/books/sets/citations.txt`; modify `tests/test_book_test_set.py`

- [ ] **Step 1: Add the check**

```python
def test_every_citation_question_names_its_gold():
    for q in load_questions(SETS / "citations.txt"):
        assert len(q.gold) == 1 and parse_reference(q.text) == q.gold[0], q.text
```

Also add `"citations.txt": (("citation",), 12, 18)` to `SET_FILES`. Run the checks. Expected: fails, because the file is missing.

- [ ] **Step 2: Write 15 citation questions**

The gold of each is the node its citation names. The mix must include each of these forms at least once (all verified to parse):
- `§ 107`
- `17 USC 512(c)(3)`, plus two or more other subsections nested two levels or deeper
- `section 101 "joint work"`, plus one more definition
- `18 U.S.C. 2319`
- `Pub. L. 105-304, sec. 401`
- `TRIPs art. 35`
- `ch. 1, n. 13`

Also use at least two other spellings (`sec.`, `§§`, `17 U.S.C. §`).

- [ ] **Step 3:** Run `uv run pytest tests/test_book_test_set.py -q`. Expected: pass.
- [ ] **Step 4:** Commit: `test(books): citation questions`

---

### Task 6: `written.txt`

**Files:** Create `evals/books/sets/written.txt`; modify `tests/test_book_test_set.py`

- [ ] **Step 1: Extend the checks**

- Add `"written.txt": (("search", "history"), 12, 18)` to `SET_FILES`.
- Add `WRITTEN_RE = re.compile(r"^# written by Claude · (search|history) · gold: .+$")`.
- Add `test_written_questions_say_who_wrote_them`: the header contains `written by Claude`, every entry's comment matches `WRITTEN_RE`, and the comment's type equals the line's type.
- Add `test_written_has_both_types`.

Run the checks. Expected: fails, because the file is missing.

- [ ] **Step 2: Write 15 questions in plain words**

Avoid the book's own wording where a plain word exists. Cover all of these:
- at least 4 `history` questions about effective dates or amendments held in notes, with gold on the note's path (e.g. `ch1-n13`);
- 2 TRIPs questions (arts. 35–38);
- 2 on criminal infringement (Title 18);
- 1 on venue or jurisdiction (Title 28);
- 2 asking for a definition without naming the term's section.

- [ ] **Step 3:** Run `uv run pytest tests/test_book_test_set.py -q`. Expected: pass.
- [ ] **Step 4:** Commit: `test(books): written questions`

---

### Task 7: The second, independent reading

**Files:** Modify `lse-search.txt`, `lse-none.txt`, `written.txt` and `tests/test_book_test_set.py`. Scratch: `$SP/testset/second-input.tsv` and `second-output.tsv`.

- [ ] **Step 1: Add the check**

`test_the_second_reading_is_recorded` checks two things:
1. The header of each of the three files contains a line matching `^# Second reading \(fresh agent, book text only\): agreed on (\d+) of (\d+) \(\d+%\)$`.
2. Every entry's comment ends with `· second reading: agreed` or matches `· second reading: .+ — settled: .+` (the owner's-check suffix from Task 8 may follow).

Run the checks. Expected: fails.

- [ ] **Step 2: Build the blind input**

`second-input.tsv` has one row per question of the three files: `n`, `file`, `type` and `question`. It carries no gold, no comments and no answerers' citations.

- [ ] **Step 3: Dispatch a fresh agent**

It gets only `second-input.tsv`, `book.jsonl`, `book-index.tsv`, and the gold rules: the spec's *Choosing the gold* and the `none` rule. It writes `second-output.tsv` with `n` and a gold list (bare paths), or `NONE` for a question it judges unanswerable from the book, plus a one-line reason. It must not read `evals/books/sets/`, the git history or Qdrant.

- [ ] **Step 4: Compare and settle**

A question is **agreed** when the two gold sets are identical, or both are NONE for `none` questions.

For each disagreement, a settling agent reads both golds against `book.jsonl`, decides, and appends ` · second reading: <their gold> — settled: <reason>`. If the decision changes the gold, it edits the question line. Agreed entries get ` · second reading: agreed`. The agreement line goes in each header.

- [ ] **Step 5:** Run `uv run pytest tests/test_book_test_set.py -q`. Expected: pass.
- [ ] **Step 6:** Commit: `test(books): second, independent reading of the gold (agreed N of M)`

---

### Task 8: The owner's check, then the freeze (STOP for the owner)

**Files:** Modify the set files as the owner directs.

- [ ] **Step 1: Draw the sample**

With `random.Random(20261118)`, draw 10 entries from `lse-search.txt`, `written.txt` and `lse-none.txt`: 3 from the disagreements settled in Task 7 (all of them if there are fewer than 3), then the rest from the other entries.

- [ ] **Step 2: Show them to the owner in one message**

For each entry, show:
- the question and its type;
- the reason;
- each gold path with its label, printed pages, and the node's full `text` from `book.jsonl`;
- for `none`, the reason no node answers it.

Ask the owner to say for each whether the passage says what the reason claims. **Stop until the owner answers.**

- [ ] **Step 3: Apply the owner's corrections**

Append ` · owner's check: <correction or confirmed>` to each sampled entry. Add a header line `# Owner's check (<date>): <n> questions, <k> corrected.` to each file that had a sampled entry.

- [ ] **Step 4:** Run `uv run pytest tests/test_book_test_set.py tests/test_book_retrieval_eval.py -q`. Expected: pass.
- [ ] **Step 5:** Commit the freeze: `test(books): test set frozen after the owner's check`

---

### Task 9: Baseline and docs

**Files:**
- Modify: `docs/rag.md`, `docs/operations.md`, `docs/architecture.md`, `docs/roadmap.md`, `docs/shipped-log.md`, `docs/wiki.md`
- Modify: the spec, `docs/superpowers/specs/README.md` and `evals/books/questions.example.txt`
- Delete: this plan

- [ ] **Step 1: Score each file**

Check that Ollama (embeddinggemma) and Qdrant (`legal_books`, 1,245 points) are up. Then, for each file:

Run: `BOOK_SEARCH_TOP_K=10 uv run python -m evals.books.run_retrieval evals/books/sets/<file> | tee $SP/testset/baseline-<file>`

Also score `lse-search.txt`'s `answers` and `governs` questions separately. Split with:

```bash
awk '/^# lse:.* · answers · /{k=1;next} /^#/{next} k&&NF{print;k=0}' evals/books/sets/lse-search.txt > $SP/testset/answers.txt
```

and the same with `governs`, then run both. **No gold changes in this task.** A miss here is a finding, not a labelling error.

- [ ] **Step 2: `rag.md`, *Measuring retrieval***

1. Replace "No test set exists yet" with the set: the four files, where their questions come from, and how to run them.
2. Add a baseline table: file, n, then Recall@5, Recall@10, MRR, nDCG@10 / hit rate / not-found, plus the answers/governs split.
3. Record the date, the commit, the settings (`BOOK_SEARCH_TOP_K=10`, `BOOK_CHUNK_MAX_TOKENS=800`, embeddinggemma, the reranker unset so it fell back, `BOOK_MIN_SCORE=0`) and the agreement rates.
4. Note that not-found is 0 by construction while the floor is off.
5. Add `history` to the type list.

- [ ] **Step 3: The other docs**

- **`operations.md`:** the retrieval-scores line names `evals/books/sets/*.txt`.
- **`architecture.md`:** the repo-tree line under `evals/books/` gains `sets/`.
- **`roadmap.md`:** item (2) of the *Index the legal reference books* row now says the set exists, with the baseline. The settings decisions and calibrating the floor stay open. The note "after the reranker is fixed, expect to calibrate twice" stays.
- **`questions.example.txt`:** its header points at `evals/books/sets/`.
- **The spec:** its Status line says built on `feat/book-test-set`, with the baseline in rag.md, and no plan link.
- **`specs/README.md`:** the entry's status.
- **`shipped-log.md`:** a row at the top.
- **`wiki.md`:** the *Last updated* date.

- [ ] **Step 4: Delete this plan**

`git rm docs/superpowers/plans/2026-11-18-book-retrieval-test-set.md` (decision `2026-10-28-plans-deleted-once-shipped`).

- [ ] **Step 5: Run the gate**

Run: `bash scripts/check.sh`
Expected: green, including `check_docs_links` and `check_wiki`.

- [ ] **Step 6:** Commit: `docs: book test set — baseline, docs; drop the plan`
