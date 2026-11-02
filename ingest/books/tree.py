# ingest/books/tree.py
"""The back end's reading of a parsed book: a node's own text, the note links, the cross-reference count.

Layout-free: it reads only the tree (models.py). How the book's lines read as running text is the
tree's `join`, which its front end supplies (Circular 92's cleans line-end hyphens and small caps).
"""
from collections.abc import Callable, Sequence

from ingest.books.models import BookTree, Node, TextLine


def own_text(node: Node, join: Callable[[Sequence[TextLine]], str]) -> str:
    """The node's own text, cleaned: its lead, then its tail (the text after its last child), each read
    on its own so nothing is joined across the children between them."""
    return " ".join(text for text in (join(node.lead), join(node.tail)) if text)


def link_notes(tree: BookTree) -> None:
    """Fill each node's `history_notes` with the paths of the notes that annotate it, in document order.

    It is a function of the notes' `annotates` alone: what a node held before is replaced, so a second run
    gives the same links. A note about a node the tree does not hold is a parse error, raised."""
    index = tree.index()
    for node in index.values():
        node.history_notes = []
    for node in tree.walk():
        if node.kind != "history_note" or node.annotates is None:
            continue
        if node.annotates not in index:
            raise ValueError(f"note {node.path} annotates {node.annotates}, which the tree does not hold")
        index[node.annotates].history_notes.append(node.path)


def xref_resolution(tree: BookTree) -> tuple[int, int, list[tuple[str, str]]]:
    """(total, resolved, misses): every cross-reference target and every unparsed reference counts once
    per node. A target resolves when its path is a node of the tree (ruling 11); the misses are (from
    path, target), and an unparsed reference is a miss as "unparsed: <raw text>" (ruling R1)."""
    paths = set(tree.index())
    total = resolved = 0
    misses: list[tuple[str, str]] = []
    for node in tree.walk():
        for target in node.xrefs:
            total += 1
            if target in paths:
                resolved += 1
            else:
                misses.append((node.path, target))
        for raw in node.xrefs_unparsed:
            total += 1
            misses.append((node.path, f"unparsed: {raw}"))
    return total, resolved, misses
