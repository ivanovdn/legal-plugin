# tests/test_book_ref_ids.py
import uuid

import pytest

from ingest.books.ref_ids import (
    POINT_NAMESPACE,
    REF_ID_PATTERN,
    REF_ID_RE,
    REF_MARKER_RE,
    chunk_key,
    find_cited_ref_ids,
    is_under,
    make_ref_id,
    parse_reference,
    point_id,
    slugify_term,
    split_ref_id,
)
from ingest.books.registry import get_book

BOOK = get_book("circ92")


def test_grammar_constants_are_the_contract():
    # Stored ids, Qdrant payloads and history all depend on these exact values.
    assert REF_ID_PATTERN == r"[a-z][a-z0-9]*-\d{4}-\d{2}:[A-Za-z0-9_-]*[A-Za-z0-9]"
    assert POINT_NAMESPACE == uuid.uuid5(uuid.NAMESPACE_URL, "legal-plugin/reference-books")
    assert str(POINT_NAMESPACE) == "b33e7abc-6e3a-5fd5-b54e-7a3c4d2d3dc3"


def test_round_trip():
    ref_id = make_ref_id(BOOK, "usc17-s122-a-4-B")
    assert ref_id == "circ92-2025-12:usc17-s122-a-4-B"
    assert split_ref_id(ref_id) == ("circ92", "2025-12", "usc17-s122-a-4-B")


@pytest.mark.parametrize(
    "path", ["", "usc17 s107", "usc17-s107-", "usc17:s107", "usc17-s107.", "§107"]
)
def test_make_ref_id_refuses_a_path_off_the_grammar(path):
    with pytest.raises(ValueError):
        make_ref_id(BOOK, path)


@pytest.mark.parametrize(
    "ref_id",
    [
        "usc17-s107",  # no book and edition
        "circ92:usc17-s107",  # no edition
        "Circ92-2025-12:usc17-s107",  # book ids are lowercase
        "circ92-2025-12:",  # no path
        "circ92-2025-12:usc17-s107.",  # a period is not part of an id
        "circ92-2025-12:usc17:s107",  # one colon only
        "circ92-2025-12:usc17-s107 ",
        "",
    ],
)
def test_split_ref_id_refuses_off_grammar(ref_id):
    with pytest.raises(ValueError):
        split_ref_id(ref_id)


def test_ref_id_re_needs_a_clean_left_edge():
    assert REF_ID_RE.search("see circ92-2025-12:usc17-s107.").group(0) == "circ92-2025-12:usc17-s107"
    assert REF_ID_RE.search("`circ92-2025-12:usc17-s107`").group(0) == "circ92-2025-12:usc17-s107"
    # Preceded by a character that can be part of an id (or a colon): not an id of ours.
    assert REF_ID_RE.search("my-circ92-2025-12:usc17-s107") is None
    assert REF_ID_RE.search("x_circ92-2025-12:usc17-s107") is None
    assert REF_ID_RE.search("ref_id:circ92-2025-12:usc17-s107") is None


def test_ref_marker_re_takes_the_id_after_the_marker():
    assert REF_MARKER_RE.search("ref_id: circ92-2025-12:usc17-s107").group(1) == "circ92-2025-12:usc17-s107"
    assert REF_MARKER_RE.search("ref_id:circ92-2025-12:usc17-s107.").group(1) == "circ92-2025-12:usc17-s107"
    assert REF_MARKER_RE.search("circ92-2025-12:usc17-s107") is None


def test_one_token_in_prose():
    t = (
        "Fair use (ref_id: circ92-2025-12:usc17-s107). See `circ92-2025-12:ch1-n40`, "
        "and circ92-2025-12:usc17-s101-def-joint-work."
    )
    assert find_cited_ref_ids(t) == [
        "circ92-2025-12:usc17-s107",
        "circ92-2025-12:ch1-n40",
        "circ92-2025-12:usc17-s101-def-joint-work",
    ]


def test_cited_ids_are_unique_in_first_seen_order():
    a, b = "circ92-2025-12:usc17-s107", "circ92-2025-12:usc17-s106"
    assert find_cited_ref_ids(f"{b} then {a} then ref_id: {b} and `{a}`") == [b, a]


def test_an_id_written_without_a_space_after_the_marker_is_still_found():
    assert find_cited_ref_ids("ref_id:circ92-2025-12:usc17-s107") == ["circ92-2025-12:usc17-s107"]


def test_an_id_glued_to_other_id_characters_is_not_cited():
    assert find_cited_ref_ids("my-circ92-2025-12:usc17-s106 and x_circ92-2025-12:usc17-s108") == []


def test_no_ids_in_plain_text():
    assert find_cited_ref_ids("Fair use is in section 107; Pub. L. No. 105-304.") == []
    assert find_cited_ref_ids("") == []


def test_chunk_keys_and_point_ids():
    r = "circ92-2025-12:usc17-s107"
    assert chunk_key(r) == r
    assert chunk_key(r, "~lead") == r + "~lead"
    assert chunk_key(r, "~lead~p2") == r + "~lead~p2"
    key = chunk_key(r, "~tail")
    pid = point_id(key)
    assert uuid.UUID(pid).version == 5
    assert pid == str(uuid.uuid5(POINT_NAMESPACE, key))
    assert point_id(key) == pid  # stable
    assert len({point_id(chunk_key(r, s)) for s in ("", "~lead", "~tail", "~p2")}) == 4


def test_point_ids_are_pinned():
    # Re-ingesting the same edition must hit the same points (no duplicates).
    # If this changes, every stored point id changes with it.
    assert point_id("circ92-2025-12:usc17-s107") == "18e12b58-985b-58a9-9074-350eeb7b5b1f"


def test_is_under():
    assert is_under("usc17-s101-def-joint-work", "usc17-s101")
    assert not is_under("usc17-s1010", "usc17-s101")
    assert is_under("usc17-s101", "usc17-s101")
    assert not is_under("usc17-s10", "usc17-s101")  # an ancestor is never longer than its descendant


@pytest.mark.parametrize(
    "text,expected",
    [
        ("§ 107", "usc17-s107"),
        ("section 107", "usc17-s107"),
        ("§ 106A", "usc17-s106a"),
        ("17 U.S.C. §122(f)(1)", "usc17-s122-f-1"),
        ("17 U.S.C. 122(a)(4)(B)", "usc17-s122-a-4-B"),
        ("18 U.S.C. § 2319", "usc18-s2319"),
        ("section 101 'joint work'", "usc17-s101-def-joint-work"),
        ("§101 (“joint work”)", "usc17-s101-def-joint-work"),
        ("TRIPs art. 38(1)", "trips-art38-1"),
        ("TRIPs Article 36", "trips-art36"),
        ("Pub. L. No. 105-304, §101", "pl105-304-s101"),
        ("ch. 1 n.3", "ch1-n3"),
        ("app. B n.2", "appB-n2"),
        ("circ92-2025-12:usc17-s107", "circ92-2025-12:usc17-s107"),
        ("usc17-s107", "usc17-s107"),
        ("what is fair use", None),
        # beyond the brief's list: what a model plausibly sends
        ("Sec. 107", "usc17-s107"),
        ("17 USC 107", "usc17-s107"),
        ("17 U.S.C. § 107.", "usc17-s107"),
        ("§ 122 (f) (1)", "usc17-s122-f-1"),
        ("  § 107  ", "usc17-s107"),
        ("§107 (a)", "usc17-s107-a"),
        ('Section 101 "joint work"', "usc17-s101-def-joint-work"),
        ("TRIPS Agreement, art. 38(1)", "trips-art38-1"),
        ("Public Law 105-304", "pl105-304"),
        ("Pub. L. No. 105-304", "pl105-304"),
        ("Pub. L. No. 105-304, section 101(a)", "pl105-304-s101-a"),
        ("Pub. L. No. 105-304, §106A", "pl105-304-s106a"),
        ("chapter 1 note 3", "ch1-n3"),
        ("appendix B note 2", "appB-n2"),
        ("app. b n.2", "appB-n2"),
        ("usc17-s101-def-joint-work", "usc17-s101-def-joint-work"),
        ("trips-art38-1", "trips-art38-1"),
        ("pl105-304-s101", "pl105-304-s101"),
        ("appB-n2", "appB-n2"),
        ("ref_id: circ92-2025-12:usc17-s107", "circ92-2025-12:usc17-s107"),
        ("`circ92-2025-12:usc17-s107`", "circ92-2025-12:usc17-s107"),
        ("", None),
        ("107", None),  # a bare number names nothing: no §, section or U.S.C.
        ("§ 107 and 108", None),
        ("17 U.S.C.", None),
        ("ch. 1", None),
        ("app. B", None),
        ("section 101 ''", None),
        ("§101(1) 'joint work'", None),
        ("section 101 '...'", None),  # a term with no letters has no slug  # a term and a subdivision together name nothing
    ],
)
def test_parse_reference(text, expected):
    assert parse_reference(text) == expected


def test_slugify_term():
    assert slugify_term("joint work") == "joint-work"
    assert slugify_term("Berne Convention work") == "berne-convention-work"
    assert slugify_term("  “Derivative work”. ") == "derivative-work"
    assert slugify_term("pictorial, graphic, and sculptural works") == "pictorial-graphic-and-sculptural-works"
    assert slugify_term("author’s work") == "authors-work"
    assert slugify_term("‘joint work’") == "joint-work"
