# Book retrieval test set — design

**Related:** [roadmap.md](../../roadmap.md) — *Index the legal reference books for retrieval-augmented checking*, open item (2) · [Reference books in RAG](2026-10-30-reference-books-design.md) (Goal 7: the runner) · [rag.md](../../rag.md#reference-books)

- **Date:** 2026-11-18
- **Status:** Built on `feat/book-test-set` (2026-11-18): the four files frozen at commit `01d2969` after the second reading and the owner's check; the baseline, scored at `01d2969` and repeated 20 times for the range ties allow, is in [rag.md](../../rag.md#measuring-retrieval). Approved by the owner 2026-11-18.
- **Branch:** `feat/book-test-set`, cut from `feat/reference-books` (unmerged), because the set needs that branch's book, runner and ref_id grammar.
- **Scope:** the question set that `evals.books.run_retrieval` scores, plus one new question type in the runner. No retrieval setting changes here.

---

## Context & motivation

### 1. Six settings are guesses until a test set exists
The book's retrieval shipped with these values still open: the chunk budget (`BOOK_CHUNK_MAX_TOKENS`, 800), the embedding model (embeddinggemma), whether BM25 indexes the chunk's header, `BOOK_MIN_SCORE` (off), whether to merge the 31 chunks under 60 tokens, and the reranker, which has never run. The runner to score them exists (`evals/books/run_retrieval.py`). Its questions file holds three format examples and nothing else.

### 2. No attorney is available, so the set is Claude's to build
The reference-books spec said the set would be "written in the attorneys' style and checked by an attorney". The owner, 2026-11-18: no attorneys are available, so the test set is Claude's; look for an existing dataset first (Hugging Face, the web), and write one only if none fits. This spec replaces that line of the earlier spec.

### 3. No ready-made set exists for Title 17 (searched 2026-11-18)
| Source | What it is | Why it doesn't fit |
|---|---|---|
| RegLab Housing Statute QA, Bar Exam QA | Retrieval sets with gold passages | State housing law and case law |
| `emre570/uscode_qac` | 115 U.S. Code questions | One is on Title 17, and it names its section |
| `WARSO46` / `kqwang` `copyrightQA` | Questions about movie plots | Unlearning research, not law |
| Copyright-Bench | Agents handling images | No questions to search with |
| **`ymoslem/Law-StackExchange`** (CC BY-SA 4.0, revision `ab2dbaad9a71b6550316994c8fcd69f24f7f16e6`) | 24,370 real questions with answers | **Fits as a source of questions**, not of gold |

Law Stack Exchange has 2,723 copyright-tagged questions. About 200 meet the pool rule below (US, an answer citing 17 U.S.C., a score of 2 or more); the file header records the exact count. The answerers' citations are hints, not gold: in a sample of 25, one answer cited §107 for an Indian-law question, one cited "§202c" (German criminal law), and several cited §106 for no point in particular.

---

## Goals
1. **About 60 `search` questions** taken word for word from Law Stack Exchange, each with gold passages chosen by reading the book.
2. **About 15 `none` questions** taken from the same site: real trademark and patent questions the book cannot answer, to calibrate `BOOK_MIN_SCORE`.
3. **About 15 `citation` questions** written from the book's structure, in mixed citation formats.
4. **About 15 hard-spot questions** written by Claude, for what nobody on the site asks: effective dates in notes, treaty articles, Titles 18 and 28, amendment history. They are kept in their own file, out of the main score.
5. **A `history` question type** in the runner, which searches with history notes included. Questions about effective dates can't be scored without it.
6. **Every gold can be audited:** each question carries its source link, what the answerers cited, and a one-line reason for the gold.
7. **The gold is checked twice before any score exists:** a second, independent reading by a fresh agent, then the owner's spot check of 10 questions.
8. **A baseline:** one scored run on today's settings, recorded with its date and settings.

## Non-goals
- **No answer-quality check.** The set scores search, not what the model writes. The two Nov 3 errors (§302 cited for co-ownership; ch. 13 called integrated circuits) are an answer-check problem, apart from the §201(a) retrieval miss this set can catch.
- **No questions written in an attorney's voice (approach B).** The owner may want them later, as a second file.
- **No setting changes.** Choosing the chunk budget, the model or the floor is the next piece of work, made with this set.
- **No foreign-copyright "near miss" questions.** A UK fair-dealing question that retrieves §107 isn't a retrieval error, and the score floor can't be expected to catch it. Whether the agent should then cite US law is a question about the agent.
- **No "any one of these" gold.** The runner counts every listed gold as needed (Recall@k is the share found). Alternatives would need a format change.
- **No fix for the reranker.** The baseline runs without it and says so.

## Anti-goals
Any of these makes the set worthless, even if every number comes out:
- **Gold chosen from search results.** Search would then be graded against its own output.
- **Questions picked or dropped by whether search finds them.**
- **Gold edited after a scored run because search missed it.** A gold changes only to fix a labelling error, with the reason written down.
- **A gold coarser than its answer.** If the answer is §512(c), a gold of §512 credits any chunk of §512 and inflates the score.
- **Claude's own wording in the main file.** It echoes the book and inflates the score: approach B's problem.
- **A missing attribution.** The source license requires it.

---

## Approaches (picked: A)

- **A. Real questions, gold chosen by Claude.** Questions are Law Stack Exchange's, word for word, so their wording owes nothing to the book. Claude decides the gold by reading the book. *Trade-off:* the gold is Claude's reading, not an attorney's, so each gold carries its reason. The questions are written by laypeople, who use fewer legal terms than attorneys, so the set leans toward the harder side for keyword search.
- **B. Claude writes every question in an attorney's voice.** *Trade-off:* the closest style to the real users, but Claude knows the passages, so the wording echoes them and the score comes out too high, and Claude would be marking its own work. Possibly later, as a separate file.
- **C. A model generates one question per passage** (the usual RAG-tooling shortcut). *Trade-off:* hundreds of questions for free, but each one is built from its passage's text (B's problem, worse), and each has a single gold, so search is marked wrong whenever it finds another passage that answers just as well.

**Picked: A** (owner, 2026-11-18: "lets start with A, later maybe B").

---

## Design

### Files
In `evals/books/sets/`, one file per source, so a score always says where its questions came from:

| File | Type(s) | Questions from | Count |
|---|---|---|---|
| `lse-search.txt` | `search` | Law Stack Exchange, word for word | ~60 |
| `lse-none.txt` | `none` | Law Stack Exchange, word for word | ~15 |
| `citations.txt` | `citation` | Claude, from the book's structure | ~15 |
| `written.txt` | `search`, `history` | Claude | ~15 |

Each file is in the runner's format. Its header comment states the source, the dataset revision, the license, the selection rule and the seed. Before each question, a comment line gives its provenance:

```
# lse:NNNNN https://law.stackexchange.com/q/NNNNN · CC BY-SA 4.0 · answerers cited: §102, §201 · answers · gold: §201(a), copyright vests in the author of the work
Who own the copyright - the artist or the subject in the drawing? | usc17-s201-a | search
```

(`NNNNN` stands for the real id. The question is copied as written, typo included.)

Dropped candidates are listed in the same file, as comments with the reason, so the selection can be audited.

### Choosing the `search` questions
- **The pool:** questions tagged `copyright`, `fair-use`, `dmca` or `public-domain`, with no foreign-jurisdiction tag, an answer that cites 17 U.S.C., and a question score of 2 or more. That is about 200 at the pinned revision; the exact count goes in the header.
- **The order:** shuffle the pool with a fixed seed, recorded in the header. Walk it in that order and take or drop each question until 60 are taken. Nothing is chosen by topic or by how search does.
- **Drop a question when:**
  - no provision in the book governs it: it turns only on what a contract or licence says, on a platform's rules, on foreign law, or on practical steps;
  - its text names a section number (that makes it a lookup, not a search);
  - it repeats a taken question's point and gold;
  - neither its title nor one sentence of its body is a question.
- **Keep a question that a provision governs, even when case law decides it** (Claude's call). Examples: is this use fair, is this an idea or an expression, is tracing a derivative work. The gold is the provision the question turns on, at its smallest node: §107, §102(b), the §101 definition. Retrieval's job is to put the governing statute in front of the model, and a passage holds that whether or not it settles the facts. These are about 30% of a sample of 40, which matches how people ask. Dropping them would tilt the set toward ownership, DMCA, term and remedies. Each provenance line marks whether the gold **answers** the question or only **governs** it, so the two kinds can be scored apart. If the "governs" questions score far higher, the broad nodes are easy to find, and the main number should then be read with that in mind.
- **The question text:** the title, word for word. When the title isn't a question by itself, use the title plus the one sentence of the body that asks it, also word for word. HTML is stripped and whitespace collapsed. Nothing is reworded.

### Choosing the gold
- **The smallest node that holds the whole answer.** Use §512(c), not §512, and the §101 definition, not §101. If a complete answer needs two provisions, both are gold, because the runner requires all of them.
- **Read from the book's text, never from search.** Gold is chosen from the parsed book (node ids and their text), with the answerers' citations as hints. The scorer is not run on a question until its file is committed.
- **Against the current text.** Answers from 2015 may describe the law before 2018. The gold is what the Dec. 2025 text says.
- **Ids are node paths** (`usc17-s203-a`), never chunk keys, so the set survives a change of the chunk budget.

### The `none` questions
- **The pool:** questions tagged `trademark` or `patents`, with none of the copyright tags above, no foreign-jurisdiction tag, and a score of 2 or more. Shuffled with the same seed.
- **Taken** only when nothing in the book answers it. The check covers Title 17, the parts of Titles 18, 28 and 44 in the book, the Public Laws and TRIPs arts. 35–38. For example, 28 U.S.C. §1338 gives jurisdiction over trademark cases, so a trademark-jurisdiction question is dropped.

### The `citation` and hand-written questions
- **`citations.txt`** mixes forms the lookup accepts:
  - `§ 107`, `17 USC 512(c)(3)`, `section 101 "joint work"`
  - `18 U.S.C. 2319`, a Public Law section, a TRIPs article
  - a chapter note (`ch. 1, n. 13`)

  Some deep subsections are included too. The gold is the node the citation names.
- **`written.txt`** covers what Law Stack Exchange never asks:
  - effective dates held in notes (`history`)
  - the TRIPs articles
  - criminal infringement (Title 18) and venue (Title 28)
  - one or two definitions asked in plain words

  Its header says Claude wrote it, and its score is reported apart from `lse-search.txt`.

### Runner change: the `history` type
`history` runs `search_book_chunks(question, include_history_notes=True)` and is scored like `search`. In the report it gets its own row. Unit tests cover the parse, the call with notes included, and the scoring, with the stubs the existing tests use.

### A second, independent reading of the gold (Claude's call)
With no attorney, the gold has one reader. So a fresh agent labels the same questions on its own. It gets only the questions and the book's text, never the first labels or the answerers' citations. Claude then settles each disagreement by reading the book and writes the reason in the provenance line. The share of questions where both readings agreed goes in the header. It is the only measure of how reliable the gold is.

### The owner's check, before any score exists (owner, 2026-11-18: option a)
The build stops after the second reading and before the first scored run. Claude shows the owner 10 questions drawn at random, including some the two readings disagreed on. Each has its gold ids, the text of each gold node from the book, and the reason. The owner checks that each passage says what its reason claims, the way they checked the 20-chunk ingest sample. Corrections are made then, with their reasons recorded. Nothing has been scored yet, so no correction can be shaped by a miss.

### Freeze, then measure
1. Commit the four files, after the owner's check.
2. Run the scorer on each file with `BOOK_SEARCH_TOP_K=10` and today's local settings (reranker unset, so it falls back).
3. Record the numbers in `rag.md`, with the date, the commit, the settings and the agreement rate.

That run is the baseline the settings work compares against. After a scored run, a gold changes only to fix a labelling error, and the fix says what was wrong.

### License
Law Stack Exchange content is CC BY-SA. Each row carries its own version: 6,745 of the 24,370 rows, including some in the pool, are 3.0, the rest 4.0. 3.0 allows adaptations to be shared under 4.0. Each provenance line records that question's own licence and link. The `lse-*.txt` headers name the dataset, its revision, the licence the file is under (CC BY-SA 4.0) and the change made ("questions copied verbatim; gold labels added"). The repo is private; if it is ever published, these files go with their license.

### Docs, in the same change
- **`rag.md`:** the set, how to run it, and the baseline.
- **Roadmap row, item (2):** "the set exists" with the baseline; the settings decisions stay open.
- **`questions.example.txt`:** its header points at the real set.
- **The specs index:** this spec.
- **`shipped-log.md`** and the date in **`wiki.md`**.

## Left to the builder
- The seed value, and how the candidate pool is extracted (a scratch script; the pool is reproducible from the revision, the filters and the seed, and the header records all three).
- Which deep subsections and formats the `citation` questions use, within the mix above.
- How exactly to dump the parsed book for reading (node id, heading, text, page).
- The wording of each drop reason and gold reason.
