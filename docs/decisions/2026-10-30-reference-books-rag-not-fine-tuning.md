# Reference books are used through retrieval, not fine-tuning or a memory layer

Date: 2026-10-30 · Decided by: the project owner · Status: decided

Related: [roadmap.md](../roadmap.md) — *Index the legal reference books for retrieval-augmented checking* · [Reference books design](../superpowers/specs/2026-10-30-reference-books-design.md)

## Context
- **What the books must do:** inform answers, with a citation the attorney can check: book, edition, section and page.
- **The constraints** in [wiki.md](../wiki.md#constraints-never-violate): every research answer cites a source, and SKILL.md is the ceiling, so there is no improvising from pretraining.
- **The size:** the first book, Circular 92, is about 269,000 tokens (220,000 for Title 17's chapters alone), at 4.46 characters per token, measured on 2026-10-30. The model's window is 131,072 tokens.

## Options
1. **Retrieval (RAG).** For each question, search the book and answer from the passages found.
2. **Fine-tune the LLM on the book.** It learns style, not reliable facts. It would produce plausible rules with plausible section numbers that may be wrong, with no pointer back to a page. It breaks the citation rule and the SKILL.md ceiling, and it must be redone at every model upgrade.
3. **A memory layer:** facts written and summarised over time, like `USER.md`. That is built for things that change. Summarising loses exact wording ("shall" against "may"), and a careful memory layer for a book is retrieval with less design.
4. **The whole book in the prompt.** It doesn't fit the window, and even the part that fits would take far longer to read than an answer can wait.

## Decision
Option 1. Every answer that uses a book names the edition it rests on, for example "as of Circular 92, Dec. 2025": the book is fixed, but the law is not.

## Why
- **Only retrieval keeps the evidence.** It is the only option that both cites a checkable source and keeps the exact wording.
- **Fine-tuning breaks two constraints:** the citation rule and the SKILL.md ceiling.
- **A model upgrade costs nothing** under retrieval.

## Open questions
- **Fine-tuning the retriever:** fine-tuning the embedding model is a different thing, and it is still open. Decide on the test set.
