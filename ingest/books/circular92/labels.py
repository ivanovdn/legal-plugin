# ingest/books/circular92/labels.py
"""U.S. Code subdivision labels, and the level each one sits at.

Level 1 is a subsection (a), 2 a paragraph (1), 3 a subparagraph (A), 4 a clause (i), 5 a
subclause (I), 6 an item (aa), 7 a subitem (AA). A label's characters name its level, except for
the ones two sequences share: (i), (v), (x) are letters and roman numerals, (I), (V), (X) capital
letters and capital numerals, (ii) a numeral and a doubled letter. For those the sequence decides:
the label is the next sibling expected at one level and not at the other. The indent decides only
what the sequence cannot (the research report's rule, 2026-10-30: in 476 of the 497 ambiguous
labels of Title 17 the sequence alone decides, and in the other 17 the indent was right).
"""

_ONES = ["", "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix"]
_TENS = ["", "x", "xx", "xxx"]

ROMANS = [_TENS[n // 10] + _ONES[n % 10] for n in range(1, 31)]
LETTERS = [chr(c) for c in range(ord("a"), ord("z") + 1)]
DOUBLED = [c * 2 for c in LETTERS]

# A subsection after (z) is (aa); a paragraph number runs as far as any section needs.
SEQ: dict[int, list[str]] = {
    1: LETTERS + DOUBLED,
    2: [str(n) for n in range(1, 120)],
    3: [label.upper() for label in LETTERS + DOUBLED],
    4: ROMANS,
    5: [r.upper() for r in ROMANS],
    6: DOUBLED,
    7: [d.upper() for d in DOUBLED],
}


def levels_of(label: str) -> list[int]:
    """Every level whose sequence holds the label, shallowest first."""
    return [level for level, seq in SEQ.items() if label in seq]


def expected_next(stack: dict[int, str], level: int) -> str | None:
    """The label the next sibling at `level` must have: the first of the sequence when the level is
    empty, else the one after the label there; None when the sequence has no next."""
    seq = SEQ[level]
    current = stack.get(level)
    if current is None:
        return seq[0]
    if current not in seq:
        return None
    position = seq.index(current) + 1
    return seq[position] if position < len(seq) else None


def is_expected(label: str, stack: dict[int, str], level: int) -> bool:
    return expected_next(stack, level) == label


def _fits(label: str, stack: dict[int, str], level: int) -> bool:
    """The label is the next sibling expected at `level`, and the level above it is open: a clause (i)
    needs a subparagraph to sit under, so (i) after (h) is subsection (i) when no (A) is open."""
    return is_expected(label, stack, level) and (level == 1 or level - 1 in stack)


def resolve(label: str, stack: dict[int, str], x_level: int) -> tuple[int, bool]:
    """(level, decided): `decided` is False when no rule placed the label: no level holds it, or
    two levels are equally near the indent and the sequence cannot choose between them."""
    levels = levels_of(label)
    if not levels:
        return x_level, False
    if len(levels) == 1:
        return levels[0], True
    fitting = [level for level in levels if _fits(label, stack, level)]
    pool = fitting or levels  # one fits: that one; both, or neither: the one nearest the indent
    pool.sort(key=lambda level: (abs(level - x_level), level))
    tied = len(pool) > 1 and abs(pool[0] - x_level) == abs(pool[1] - x_level)
    return pool[0], not tied


def resolve_level(label: str, stack: dict[int, str], x_level: int) -> int:
    """The level of `label`, given the labels open above it (`stack`: level -> label) and the
    level its indent suggests. One level holds it: that one. Several: the one where it is the
    expected next sibling (with its parent level open); if that is several or none, the one
    nearest the indent."""
    return resolve(label, stack, x_level)[0]
