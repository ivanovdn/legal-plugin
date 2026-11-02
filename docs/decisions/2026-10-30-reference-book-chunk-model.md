# Reference-book chunks get their own model and collection, with no `client_id`

Date: 2026-10-30 · Decided by: the project owner · Status: decided

Related: [roadmap.md](../roadmap.md) — *Index the legal reference books for retrieval-augmented checking*, *Bind `client_id` to the agents' RAG tools* · [Reference books design](../superpowers/specs/2026-10-30-reference-books-design.md) · [keyword search in Qdrant](2026-10-30-keyword-search-in-qdrant.md) · [parser](2026-10-30-reference-book-parser.md) · [citations](2026-10-30-reference-book-citations.md)

## Context
- **Today's chunk model is built for contracts.** Every chunk is a [`LegalChunk`](../../ingest/chunk_models.py) with `client_id` required. Its comment suggests `"internal"` for shared documents. But `"internal"` is the default tenant: [`intake`](../../graph/nodes/intake.py) gives it to every unmapped user, and the demo MSA is stored under it.
- **The rule as it stood:** rule 3 in [CLAUDE.md](../../CLAUDE.md#hard-rules-from-memory--past-incidents) and constraint 5 in [wiki.md](../wiki.md#constraints-never-violate) said every retrieval filters by `client_id`. A book belongs to no client.
- **A book chunk needs fields `LegalChunk` lacks:** book, edition, citation path, source title, pages, kind, parent headings, cross-references, linked notes and a stable id. It has no use for `client_id`, `sensitivity` or `clause_type`.
- **Re-ingest duplicates.** Ingest assigns a random `doc_id` on every run, so ingesting a document twice duplicates its points.

## Options
1. **Reuse `LegalChunk`, with books under `client_id="internal"`.** The book fields get squeezed into strings, with no pages or kind. And `"internal"` is a real tenant.
2. **Reuse `LegalChunk` with a sentinel tenant (`"public"`) that client searches also match.** Tenancy then depends on an "or" that every search path must apply.
3. **Generalise `LegalChunk` and make `client_id` optional.** A contract chunk without a tenant would then pass validation.
4. **A shared base model with two subclasses.** It couples two models for the sake of one shared consumer (`upsert_chunks`), and every call site changes now.
5. **Books in `legal_docs`, marked `doc_type="reference_book"`.** Two exceptions to forget, one on each side: client searches must exclude the type, and book searches must skip the `client_id` filter.
6. **A separate `BookChunk` with no `client_id`, in its own collection, reached only by its own tools.**

## Decision
Option 6, with the rule reworded and five details fixed:
- **The rule, reworded** in CLAUDE.md rule 3 and wiki constraint 5: "The `client_id` filter applies to collections that hold client documents. The book collection holds no client data."
- **A book registry** in the repo, which lists each book's:
  - id, title and edition;
  - jurisdiction, source and licence;
  - the SHA-256 of its PDF.

  The ingest command accepts only a registered book whose file matches, so nothing else is ever written to the book collection.
- **Two classifying fields:** `jurisdiction`, for a later filter, and `source_type`, which says what law a chunk is: a U.S.C. title, an uncodified Act or a treaty. It sits beside `kind`, which says what part of the book it is.
- **The edition is part of the `ref_id`,** for example `circ92-2025-12:usc17-s122-f-1`. Point ids are a UUIDv5 of the `ref_id`, so re-ingesting an edition replaces its own points. A stored answer's `ref_id` never resolves to a later edition's text.
- **One collection for all books.** The book tools name it in code.

## Why
- **The deciding fact:** `"internal"` is the default tenant, not "nobody's". Options 1 and 2 put the book inside a real tenant or next to one. Options 2 and 5 make tenancy depend on an exception being applied everywhere.
- **Option 3 weakens the rule** for exactly the data it protects.
- **The rule can't break on the book side.** With no `client_id` field there, there is nothing to filter, and the registry check keeps client files out.

## Open questions
- **`search_legal`'s `collection` argument:** the model fills it in, so it can name the book collection. That's harmless, because the book is public, but the tool should accept only client collections. This ties to *Bind `client_id` to the agents' RAG tools*.
- **The embedding model:** the separate collection could use its own. embeddinggemma stays until the test set shows otherwise.
