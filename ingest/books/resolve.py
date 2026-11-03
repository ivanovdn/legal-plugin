# ingest/books/resolve.py
"""What a citation of a node needs, read from the chunks that hold it (spec D1, D8).

`resolve_nodes` is the pure step of `rag.book_search.fetch_nodes`: given the chunks Qdrant returned, it
says, for each ref_id, its label, kind, source type, appendix, page span, and the chunk ids a citation of
it is valid through. The dry run runs the same function over the whole chunk list (report.py), so the bar
checks the code that pages a citation, not a copy of it.
"""
from collections.abc import Iterable
from dataclasses import dataclass

from ingest.books.models import BookChunk, SubRef


@dataclass
class NodeInfo:
    ref_id: str
    label: str
    kind: str
    source_type: str
    appendix: str | None
    page_start: int
    page_end: int
    # The chunk ref_ids a citation of this node is valid through: retrieving any of them put the node in
    # front of the model. Its own id when it has a chunk of its own, or when it has none (then a citation
    # of it is valid exactly when it was itself retrieved: lookup does that for a section whose text is
    # all in its subsections); the containing chunk's id for a node inside a chunk.
    containers: set[str]


def resolve_nodes(ref_ids: Iterable[str], chunks: Iterable[BookChunk]) -> dict[str, NodeInfo]:
    """What a citation of each id needs, for the ids `chunks` hold; an id they don't hold is absent.

    An id is held through ANY of: a chunk whose `ref_id` it is; a chunk whose `sub_ref_ids` hold it (a node
    inside a chunk); a chunk whose `ancestor_ref_ids` hold it (a node with no chunk of its own, such as a
    section whose text is all in its subsections, which appears only in its descendants' lists).

    - label and pages: from what holds it, each exact by construction: its own chunk's first
      `ancestor_labels` / `ancestor_pages` entry (the node itself: its whole subtree), else its SubRef's,
      else its entry in the `ancestor_*` lists of a chunk below it. Never the span of the chunks below: a
      lead the chunker dropped (a bare heading, "(d) Statutory License … Systems.—") has no chunk, so its
      page would be lost (final review C1: §111(d) paged 34–38 for 33–38).
    - containers, kind, source_type, appendix: see `NodeInfo`; the kind and the rest come from a chunk
      that holds the node (its own when it has one).
    """
    wanted = list(dict.fromkeys(ref_ids))
    want = set(wanted)
    own: dict[str, list[BookChunk]] = {}
    above: dict[str, tuple[BookChunk, int]] = {}  # the first chunk below the node, and the node's index there
    inside: dict[str, list[tuple[BookChunk, SubRef]]] = {}
    for chunk in chunks:
        if chunk.ref_id in want:
            own.setdefault(chunk.ref_id, []).append(chunk)
        for i, ancestor in enumerate(chunk.ancestor_ref_ids):
            if ancestor in want:
                above.setdefault(ancestor, (chunk, i))
        for sub in chunk.sub_refs:
            if sub.ref_id in want:
                inside.setdefault(sub.ref_id, []).append((chunk, sub))

    nodes: dict[str, NodeInfo] = {}
    for ref_id in wanted:
        its_own, its_inside = own.get(ref_id, []), inside.get(ref_id, [])
        if its_own:
            holder = its_own[0]
            label, (page_start, page_end) = holder.label, holder.ancestor_pages[holder.ancestor_ref_ids.index(ref_id)]
        elif its_inside:
            holder, sub = its_inside[0]
            label = sub.label
            page_start = min(s.page_start for _, s in its_inside)
            page_end = max(s.page_end for _, s in its_inside)
        elif ref_id in above:
            holder, i = above[ref_id]
            label, (page_start, page_end) = holder.ancestor_labels[i], holder.ancestor_pages[i]
        else:
            continue

        containers = {c.ref_id for c in its_own} | {c.ref_id for c, _ in its_inside}
        nodes[ref_id] = NodeInfo(
            ref_id=ref_id,
            label=label,
            kind=holder.kind,
            source_type=holder.source_type,
            appendix=holder.appendix,
            page_start=page_start,
            page_end=page_end,
            containers=containers or {ref_id},
        )
    return nodes
