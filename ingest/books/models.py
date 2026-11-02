# ingest/books/models.py
"""What a book is once parsed, and what one chunk of it is once stored.

The front end builds a `BookTree` of `Node`s (a section, a subdivision, a definition, a note);
the chunker cuts it into `BookChunk`s, which is what Qdrant holds. A `ref_id` names a node, a
chunk has its own key (ref_ids.py).
"""
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator, model_validator

from ingest.books.ref_ids import point_id
from ingest.books.registry import BookEntry

KINDS = ("rule", "definition", "history_note", "repealed")
SOURCE_TYPES = ("usc_title", "uncodified_act", "treaty")


def _check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"kind {kind!r} is not one of {KINDS}")
    return kind


def _check_source_type(source_type: str) -> str:
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"source_type {source_type!r} is not one of {SOURCE_TYPES}")
    return source_type


@dataclass
class TextLine:
    text: str
    page: int  # the printed page, not the PDF page
    marks: list[int] = field(default_factory=list)  # footnote markers lifted out of this line
    para_start: bool = False


@dataclass
class Node:
    path: str  # the part of the ref_id after the colon
    kind: str
    source_type: str
    jurisdiction: str
    label: str  # as cited: "17 U.S.C. §122(f)(1)"
    heading: str
    scope: str  # the chapter or appendix it sits in
    lead: list[TextLine] = field(default_factory=list)  # text before the first child
    children: list["Node"] = field(default_factory=list)
    tail: list[TextLine] = field(default_factory=list)  # text after the last child
    appendix: str | None = None
    term: str | None = None  # the defined term, for a definition
    note_number: int | None = None
    annotates: str | None = None  # a note: the path of the node whose marker it explains
    xrefs: list[str] = field(default_factory=list)  # paths of the law this node names
    xrefs_unparsed: list[str] = field(default_factory=list)  # raw reference texts that name law but yield no path
    history_notes: list[str] = field(default_factory=list)  # paths of the notes that annotate this node
    effective_date: bool = False

    def __post_init__(self):
        _check_kind(self.kind)
        _check_source_type(self.source_type)

    def walk(self) -> Iterator["Node"]:
        """This node, then every descendant, depth first in document order."""
        yield self
        for child in self.children:
            yield from child.walk()

    def own_lines(self) -> list[TextLine]:
        """This node's text without its children's: the lead, then the tail."""
        return [*self.lead, *self.tail]

    def all_lines(self) -> list[TextLine]:
        """Everything under this node, in document order."""
        lines = list(self.lead)
        for child in self.children:
            lines.extend(child.all_lines())
        lines.extend(self.tail)
        return lines

    def pages(self) -> tuple[int, int]:
        """First and last printed page of everything under this node."""
        lines = self.all_lines()
        if not lines:
            raise ValueError(f"node {self.path!r} has no text lines, so no pages")
        pages = [line.page for line in lines]
        return min(pages), max(pages)


def plain_join(lines: Sequence[TextLine]) -> str:
    """Lines -> running text with no cleaning: their texts, stripped, one space apart."""
    return " ".join(text for line in lines if (text := line.text.strip()))


@dataclass
class BookTree:
    book: BookEntry
    nodes: list[Node]  # the roots, in document order
    # How the book's lines read as running text. The front end supplies its own (Circular 92's cleans
    # line-end hyphens and small caps); the plain join is for a tree built by hand.
    join: Callable[[Sequence[TextLine]], str] = plain_join

    def walk(self) -> Iterator[Node]:
        for node in self.nodes:
            yield from node.walk()

    def index(self) -> dict[str, Node]:
        by_path: dict[str, Node] = {}
        for node in self.walk():
            if node.path in by_path:
                raise ValueError(f"two nodes share the path {node.path!r}")
            by_path[node.path] = node
        return by_path


class SubRef(BaseModel):
    """A node inside a chunk, with its own pages, so a citation below the chunk can be checked and paged."""

    ref_id: str
    label: str
    page_start: int
    page_end: int


class BookChunk(BaseModel):
    chunk_key: str
    ref_id: str
    book: str
    edition: str
    path: str
    kind: str
    source_type: str
    jurisdiction: str
    label: str
    heading: str
    appendix: str | None = None
    page_start: int
    page_end: int
    text: str  # header + "\n" + body: what is embedded
    body: str  # what BM25 indexes and what the tools show
    # The embedding model's count of `text`; -1 when the counter reported the text over the embedding window.
    tokens: int
    order: int
    ancestor_ref_ids: list[str] = Field(default_factory=list)  # self, then parent, … root
    # The labels of the same nodes, index for index: a node with no chunk of its own (a section with no
    # text before its first subsection) is cited and rendered from a chunk below it.
    ancestor_labels: list[str] = Field(default_factory=list)
    sub_refs: list[SubRef] = Field(default_factory=list)
    sub_ref_ids: list[str] = Field(default_factory=list)
    xrefs: list[str] = Field(default_factory=list)
    history_notes: list[str] = Field(default_factory=list)  # prefixed ref_ids
    effective_date_notes: list[str] = Field(default_factory=list)  # prefixed ref_ids

    @field_validator("kind")
    @classmethod
    def _kind_is_known(cls, kind: str) -> str:
        return _check_kind(kind)

    @field_validator("source_type")
    @classmethod
    def _source_type_is_known(cls, source_type: str) -> str:
        return _check_source_type(source_type)

    @model_validator(mode="after")
    def _ancestor_lists_pair_up(self) -> "BookChunk":
        if len(self.ancestor_labels) != len(self.ancestor_ref_ids):
            raise ValueError(
                f"ancestor_labels has {len(self.ancestor_labels)} entries and ancestor_ref_ids "
                f"{len(self.ancestor_ref_ids)}: they name the same nodes, index for index"
            )
        return self

    @property
    def point_id(self) -> str:
        return point_id(self.chunk_key)
