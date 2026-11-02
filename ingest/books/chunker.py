# ingest/books/chunker.py
"""The back end's cut: a BookTree -> BookChunks under a token budget (spec D4; rulings 9, 20 and R4).

A node that fits the budget is one chunk, keyed by its ref_id, with every node below it as a sub-ref.
One that does not splits by its structure (the prototype's `split()`): its lead (the text before its
first child) is `<ref_id>~lead`, each child is cut the same way, and its tail (the text after its last
child) is `<ref_id>~tail`. A lead that is only its label and heading ("(f) Heading.—") makes no chunk:
the heading is carried in the children's headers. §101 always gives one chunk per definition, plus one
for its opening sentence. A text run still over the budget (a leaf, a lead, a tail, a note, a
definition) splits at paragraph starts, then at sentence ends: `<key>`, `<key>~p2`, `<key>~p3`…

A single sentence over the budget stays whole: the chunker never drops or cuts text. Its `tokens` is
then over the budget, or -1 when the counter says it is past the embedding window (R4), and the dry run
reports it.

Boundaries move with the budget and the tokenizer; the ref_ids chunks carry are node paths and never
move (D1).
"""
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace

from ingest.books.models import BookChunk, BookTree, Node, SubRef, TextLine
from ingest.books.ref_ids import chunk_key, make_ref_id
from ingest.books.tokens import CachedTokenCounter, TokenCounter

HISTORY_NOTE_HEADING = "amendment history, not statutory text"
TREATY_NOTICE = " (treaty, not U.S. law)"
HEADING_SEPARATOR = " › "
OVER_WINDOW = -1  # `tokens` of a chunk the counter says is past the embedding window (R4)

# A lead's labels before its heading: "(f)", "(a)(1)", "[(i)".
_LEADING_LABELS_RE = re.compile(r"^\[?(?:\(\s*[A-Za-z0-9]+\s*\)\s*)+")

# --- sentence ends -----------------------------------------------------------------------------------
# A sentence ends at . ? or !, with any closing quotes or brackets after it, before a word that starts
# with a capital (or an opening quote or bracket and a capital). Not after an abbreviation: a single
# letter ("Pub. L. No."), a dotted one ("U.S.C."), or one of these.
_CLOSERS = "\"”’')\\]"
_IN_LINE_END_RE = re.compile(rf"[.?!][{_CLOSERS}]*(?=\s+[\"“‘(\[]?[A-Z])")
_LINE_END_RE = re.compile(rf"[.?!][{_CLOSERS}]*$")
_STARTS_SENTENCE_RE = re.compile(r"[\"“‘(\[]?[A-Z]")
_LAST_WORD_RE = re.compile(rf"(\S*?)[.?!][{_CLOSERS}]*$")
_ABBREVIATIONS = frozenset({
    "Pub", "No", "Nos", "Stat", "Sec", "Secs", "Cong", "Sess", "Rep", "Doc", "Res", "Reg", "Fed", "Supp",
    "Art", "art", "arts", "Ch", "ch", "chs", "cl", "par", "para", "subpar", "subch", "subsec", "pt", "Pt",
    "et", "al", "seq", "v", "vs", "Inc", "Co", "Corp", "Ltd", "Bros", "Mr", "Mrs", "Ms", "Dr", "St",
    "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct", "Nov", "Dec",
    "Id", "id", "Ed", "ed", "eds", "app", "n", "nn", "p", "pp", "Vol", "vol", "Div", "Tit", "Exec",
})


def _ends_sentence(head: str) -> bool:
    """`head` ends at . ? or ! (and its closers): True unless that is an abbreviation's period."""
    word = _LAST_WORD_RE.search(head).group(1).lstrip("(\"“‘[")
    if "." in word or (len(word) == 1 and word.isalpha()):
        return False
    return word not in _ABBREVIATIONS


def _sentence_cuts(text: str, following: str) -> Iterator[int]:
    """Where a line's text may be cut after a sentence: inside it, and at its end when the next line of
    the paragraph starts a sentence."""
    for m in _IN_LINE_END_RE.finditer(text):
        if _ends_sentence(text[: m.end()]):
            yield m.end()
    if following and _LINE_END_RE.search(text) and _STARTS_SENTENCE_RE.match(following) and _ends_sentence(text):
        yield len(text)


def _sentences(paragraph: Sequence[TextLine]) -> list[list[TextLine]]:
    """A paragraph's lines -> its sentences, a line cut where a sentence ends inside it (each piece keeps
    the line's page). A one-word "sentence" is a label ("1."), never a sentence of its own."""
    sentences: list[list[TextLine]] = []
    current: list[TextLine] = []
    words = 0
    for i, line in enumerate(paragraph):
        text = line.text.strip()
        following = paragraph[i + 1].text.strip() if i + 1 < len(paragraph) else ""
        start = 0
        for cut in _sentence_cuts(text, following):
            piece = text[start:cut].strip()
            if words + len(piece.split()) < 2:
                continue
            current.append(_piece(line, piece, first=start == 0))
            sentences.append(current)
            current, words, start = [], 0, cut
        rest = text[start:].strip()
        if rest:
            current.append(_piece(line, rest, first=start == 0))
            words += len(rest.split())
    if current:
        sentences.append(current)
    return sentences


def _piece(line: TextLine, text: str, *, first: bool) -> TextLine:
    if first and text == line.text.strip():
        return line
    return replace(line, text=text, marks=list(line.marks) if first else [], para_start=line.para_start and first)


# --- paragraphs and bodies ---------------------------------------------------------------------------

def _paragraphs(lines: Sequence[TextLine]) -> list[list[TextLine]]:
    """Lines -> paragraphs: a new one at each line that starts a paragraph."""
    paragraphs: list[list[TextLine]] = []
    for line in lines:
        if line.para_start or not paragraphs:
            paragraphs.append([line])
        else:
            paragraphs[-1].append(line)
    return paragraphs


def _node_paragraphs(node: Node) -> list[list[TextLine]]:
    """Every paragraph under the node in document order, each node's text read on its own: nothing is
    joined across the boundary between a node and its child."""
    paragraphs = _paragraphs(node.lead)
    for child in node.children:
        paragraphs.extend(_node_paragraphs(child))
    paragraphs.extend(_paragraphs(node.tail))
    return paragraphs


def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _defines_terms(node: Node) -> bool:
    """§101's shape: its children are definitions, and each gets a chunk of its own (D4)."""
    return any(child.kind == "definition" and child.term is not None for child in node.children)


def _heading_path(ancestors: Sequence[Node], node: Node) -> str:
    """The section's title, then each non-empty heading of the units between it and the node (ruling 20).
    The node's own heading is in its body; a root's is its title."""
    root = ancestors[0] if ancestors else node
    return HEADING_SEPARATOR.join(h for h in (root.heading, *(a.heading for a in ancestors[1:])) if h)


@dataclass
class _Chunker:
    tree: BookTree
    budget: int
    count: TokenCounter
    index: dict[str, Node] = field(init=False)
    chunks: list[BookChunk] = field(default_factory=list)

    def __post_init__(self):
        self.index = self.tree.index()

    def ref(self, path: str) -> str:
        return make_ref_id(self.tree.book, path)

    def body(self, paragraphs: Sequence[Sequence[TextLine]]) -> str:
        return "\n".join(text for paragraph in paragraphs if (text := self.tree.join(paragraph)))

    def fits(self, header: str, paragraphs: Sequence[Sequence[TextLine]]) -> bool:
        tokens = self.count(f"{header}\n{self.body(paragraphs)}")
        return tokens is not None and tokens <= self.budget

    def header(self, node: Node, ancestors: Sequence[Node]) -> tuple[str, str]:
        """(the text's first line, the `heading` payload)."""
        if node.kind == "history_note":
            heading = HISTORY_NOTE_HEADING
            header = f"{node.label} — {heading} — on {self.annotated_label(node)}"
        else:
            heading = _heading_path(ancestors, node)
            header = f"{node.label} — {heading}" if heading else node.label
        if node.source_type == "treaty":
            header += TREATY_NOTICE
        return header, heading

    def annotated_label(self, note: Node) -> str:
        if note.annotates is None:
            return note.scope  # a note on a chapter or appendix as a whole
        annotated = self.index.get(note.annotates)
        if annotated is None:
            raise ValueError(f"note {note.path} annotates {note.annotates}, which the tree does not hold")
        return annotated.label

    def is_bare_heading(self, node: Node) -> bool:
        """The lead is only the node's labels and heading ("(f) Heading.—"), so it says nothing a child's
        header doesn't."""
        if not node.heading or not node.lead:
            return False
        return _letters(_LEADING_LABELS_RE.sub("", self.tree.join(node.lead))) == _letters(node.heading)

    # --- the cut ---------------------------------------------------------------------------------

    def node(self, node: Node, ancestors: list[Node]) -> None:
        header, heading = self.header(node, ancestors)
        whole = _node_paragraphs(node)
        if not _defines_terms(node) and self.fits(header, whole):
            self.emit(node, ancestors, "", whole, list(node.walk())[1:], header, heading)
            return
        if not node.children:
            self.run(node, ancestors, "", [*node.lead, *node.tail], header, heading)
            return
        if node.lead and not self.is_bare_heading(node):
            self.run(node, ancestors, "~lead", node.lead, header, heading)
        for child in node.children:
            self.node(child, [*ancestors, node])
        if node.tail:
            self.run(node, ancestors, "~tail", node.tail, header, heading)

    def run(self, node: Node, ancestors: list[Node], suffix: str, lines: list[TextLine], header: str,
            heading: str) -> None:
        for i, part in enumerate(self.parts(header, lines), start=1):
            key_suffix = suffix if i == 1 else f"{suffix}~p{i}"
            self.emit(node, ancestors, key_suffix, _paragraphs(part), [], header, heading)

    def parts(self, header: str, lines: list[TextLine]) -> list[list[TextLine]]:
        """A text run -> parts under the budget (ruling 9): paragraphs packed while they fit; a paragraph
        over the budget alone is cut at sentence ends, its sentences packed the same way, and its parts
        closed at its end, so the next paragraph starts a part."""

        def fits(part: list[TextLine]) -> bool:
            return self.fits(header, _paragraphs(part))

        if fits(lines):
            return [lines]
        parts: list[list[TextLine]] = []
        current: list[TextLine] = []
        for paragraph in _paragraphs(lines):
            if fits([*current, *paragraph]):
                current.extend(paragraph)
                continue
            if current:
                parts.append(current)
            current = list(paragraph)
            if fits(current):
                continue
            current = []
            for sentence in _sentences(paragraph):
                if current and fits([*current, *sentence]):
                    current.extend(sentence)
                    continue
                if current:
                    parts.append(current)
                current = list(sentence)  # one sentence over the budget stays whole (R4)
            parts.append(current)
            current = []
        if current:
            parts.append(current)
        return parts

    def emit(self, node: Node, ancestors: list[Node], suffix: str, paragraphs: list[list[TextLine]],
             inside: list[Node], header: str, heading: str) -> None:
        body = self.body(paragraphs)
        if not body.strip():
            raise ValueError(f"{node.path}{suffix} has no text: a chunk needs a body (BM25 indexes it)")
        text = f"{header}\n{body}"
        tokens = self.count(text)
        pages = [line.page for paragraph in paragraphs for line in paragraph]
        # The chunk's text is part of every node above it, so their notes apply to it; then its own
        # node's, and those of the nodes inside it.
        noted = [*ancestors, node, *inside]
        notes = list(dict.fromkeys(path for n in noted for path in n.history_notes))
        sub_refs = [self.sub_ref(n) for n in inside]
        ref_id = self.ref(node.path)
        self.chunks.append(BookChunk(
            chunk_key=chunk_key(ref_id, suffix),
            ref_id=ref_id,
            book=self.tree.book.id,
            edition=self.tree.book.edition_id,
            path=node.path,
            kind=node.kind,
            source_type=node.source_type,
            jurisdiction=node.jurisdiction,
            label=node.label,
            heading=heading,
            appendix=node.appendix,
            page_start=min(pages),
            page_end=max(pages),
            text=text,
            body=body,
            tokens=OVER_WINDOW if tokens is None else tokens,
            order=len(self.chunks),
            ancestor_ref_ids=[ref_id, *(self.ref(a.path) for a in reversed(ancestors))],
            sub_refs=sub_refs,
            sub_ref_ids=[s.ref_id for s in sub_refs],
            xrefs=list(dict.fromkeys(x for n in (node, *inside) for x in n.xrefs)),
            history_notes=[self.ref(path) for path in notes],
            effective_date_notes=[self.ref(path) for path in notes if self.note(path).effective_date],
        ))

    def sub_ref(self, node: Node) -> SubRef:
        """A node inside a chunk, paged by its own subtree, so a citation of it gets its own pages."""
        first, last = node.pages()
        return SubRef(ref_id=self.ref(node.path), label=node.label, page_start=first, page_end=last)

    def note(self, path: str) -> Node:
        note = self.index.get(path)
        if note is None or note.kind != "history_note":
            raise ValueError(f"{path} is linked as a history note, but the tree holds no such note")
        return note


def chunk_tree(tree: BookTree, *, budget: int, count_tokens: TokenCounter) -> list[BookChunk]:
    """The whole tree cut into chunks, in document order (`order` counts up across the book).

    `budget` is in the counter's tokens, header included (BOOK_CHUNK_MAX_TOKENS). Each text is counted
    once, however often the cut asks."""
    if budget < 1:
        raise ValueError(f"the chunk budget must be a positive number of tokens, not {budget}")
    count = count_tokens if isinstance(count_tokens, CachedTokenCounter) else CachedTokenCounter(count_tokens)
    chunker = _Chunker(tree, budget, count)
    for root in tree.nodes:
        chunker.node(root, [])
    return chunker.chunks
