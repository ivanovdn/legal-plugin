# Reference books get their own parser

Date: 2026-10-30 · Decided by: the project owner · Status: decided

Related: [roadmap.md](../roadmap.md) — *Index the legal reference books for retrieval-augmented checking* · [Reference books design](../superpowers/specs/2026-10-30-reference-books-design.md) · [chunk model](2026-10-30-reference-book-chunk-model.md)

## Context
The first book is Circular 92 (Dec. 2025): Title 17 U.S.C. with appendices A–T, 486 pages, a digital PDF in one text column. It was measured in the Circular 92 exploration of 2026-10-30. The report and the prototype scripts are in the research vault, under `research/reference-books/claude-code/`.
- **Today's parser fails on it.** Run on the book, [`pdf_parser.py`](../../ingest/parsers/pdf_parser.py) takes section labels from body lines ("section 802 of this title…") and starts the fair-use chunk inside §106A(e)(2). It also leaves 420 running footers and 97 footnote markers in the text, and records no pages.
- **The structure can be recovered by rules:**
  - footnote markers are found by their font (192 of 192 matched);
  - labels (a)(1)(A)(i)… are read from their characters and sequence, with the indent breaking ties: 99.4% sit at their level, and the 4 failures are all in §506(a)(3);
  - printed page = PDF page − 22.
- **Routing:** [`ingest/pipeline.py`](../../ingest/pipeline.py) picks a parser by file extension, so a book PDF would reach the contract parser.

## Options
1. **Extend `pdf_parser.py` with a book mode.** What failed on the book is that module's core (its heading regex, word-count chunker and random `doc_id`). A book mode would replace the core behind a flag, and a change made for books could break contract ingest.
2. **A new book parser with its own admin command.** It has two halves:
   - a layout front end: fonts, indents and pages → a tree of labelled nodes;
   - a layout-free back end: tree → chunks, cross-references and citations.

   The cost is a second parser to maintain, and the front end is specific to this book's typesetting.
3. **One general parser for any legal book.** With one book to learn from, general rules would be guesses.
4. **An off-the-shelf PDF-structure tool,** such as docling or unstructured. Not tried. None knows the U.S. Code's label rules or this book's footnote font, so the label logic would stay ours. It adds a dependency, and some of these tools need a layout model copied onto the air-gapped side.
5. **The official U.S. Code XML (USLM) instead of the PDF.** Not tried. It has no Circular 92 page numbers, which the citation needs. It also lacks TRIPs, may come from another release point, and is no help for books that aren't U.S. law.
6. **Let the LLM parse the pages.** Results vary between runs and models, errors are hard to see, 486 pages is slow, and the measured rules already reach 99.4%.

## Decision
Option 2, with three conditions:
- **Ingest is an admin command.** The add-in's upload and `POST /api/ingest` can never reach the book parser or the book collection.
- **Hand fixes are an overrides file** per book and edition. Each entry gives its reason, for example "§506(a)(3): list set one level too deep, p. 204".
- **The golden file is fixed first.** The ch.1 + ch.5 chunk list (568 rows) becomes the test oracle only after its known bugs are fixed, or marked as expected failures:
  - the 4 labels in §506(a)(3);
  - ch.5 n.8, whose "section 2323" is in 18 U.S.C.;
  - §504(c)'s half-resolved Act reference.

  Otherwise the tests would guarantee the bugs.

## Why
- **The deciding fact:** the parts of `pdf_parser.py` worth reusing, pdfplumber and the embed-and-upsert step, aren't parser code. The parser code is what failed.
- **The rules are already measured** on the whole book, so the work is moving known rules into tested code.
- **The split pays off at the second book,** which will replace only the front end. Option 3 can wait for that book.

## Open questions
- **The USLM XML as a check:** it could verify the parse rather than replace it.
- **The second book** will show whether the front/back split holds.
