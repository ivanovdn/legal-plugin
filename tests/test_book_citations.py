# tests/test_book_citations.py
import pytest

from ingest.books.citations import (
    UNVERIFIED,
    find_stated_pages,
    page_span,
    render_citation,
    render_response,
)
from ingest.books.ref_ids import find_cited_ref_ids
from ingest.books.registry import get_book

book = get_book("circ92")


@pytest.mark.parametrize(
    "kw,expected",
    [
        (
            dict(label="17 U.S.C. §122(f)(1)", kind="rule", source_type="usc_title", appendix=None, page_start=143, page_end=143),
            "17 U.S.C. §122(f)(1) — Circular 92 (Dec. 2025), p. 143",
        ),
        (
            dict(label="17 U.S.C. §101 (“joint work”)", kind="definition", source_type="usc_title", appendix=None, page_start=4, page_end=4),
            "17 U.S.C. §101 (“joint work”) — Circular 92 (Dec. 2025), p. 4",
        ),
        (
            dict(label="18 U.S.C. §2319", kind="rule", source_type="usc_title", appendix="N", page_start=428, page_end=428),
            "18 U.S.C. §2319 — Circular 92 (Dec. 2025), app. N, p. 428",
        ),
        (
            dict(
                label="Digital Millennium Copyright Act, Pub. L. No. 105-304, §101 (1998)",
                kind="rule",
                source_type="uncodified_act",
                appendix="B",
                page_start=371,
                page_end=371,
            ),
            "Digital Millennium Copyright Act, Pub. L. No. 105-304, §101 (1998) — Circular 92 (Dec. 2025), app. B, p. 371",
        ),
        (
            dict(label="ch. 1 n.3", kind="history_note", source_type="usc_title", appendix=None, page_start=146, page_end=146),
            "Circular 92 (Dec. 2025), ch. 1 n.3, p. 146 (amendment history, not statutory text)",
        ),
        (
            dict(label="TRIPs Agreement, art. 38(1)", kind="rule", source_type="treaty", appendix="S", page_start=458, page_end=458),
            "TRIPs Agreement, art. 38(1) — Circular 92 (Dec. 2025), app. S, p. 458 (treaty, not U.S. law)",
        ),
        (
            dict(label="17 U.S.C. §509", kind="repealed", source_type="usc_title", appendix=None, page_start=206, page_end=206),
            "17 U.S.C. §509 — Circular 92 (Dec. 2025), p. 206 (repealed)",
        ),
        # a chunk spanning two pages: the whole span
        (
            dict(label="17 U.S.C. §122(f)", kind="rule", source_type="usc_title", appendix=None, page_start=143, page_end=144),
            "17 U.S.C. §122(f) — Circular 92 (Dec. 2025), pp. 143–44",
        ),
        # a note in an appendix names its appendix in its label, and the citation does not repeat it
        (
            dict(label="app. B n.2", kind="history_note", source_type="uncodified_act", appendix="B", page_start=380, page_end=381),
            "Circular 92 (Dec. 2025), app. B n.2, pp. 380–81 (amendment history, not statutory text)",
        ),
        # a repealed stub never reads as live text, whatever its source
        (
            dict(label="17 U.S.C. §101 (“Berne Convention work”)", kind="repealed", source_type="usc_title", appendix="T", page_start=484, page_end=484),
            "17 U.S.C. §101 (“Berne Convention work”) — Circular 92 (Dec. 2025), app. T, p. 484 (repealed)",
        ),
    ],
)
def test_render_citation(kw, expected):
    assert render_citation(book=book, **kw) == expected


@pytest.mark.parametrize(
    "kw",
    [
        dict(kind="statute", source_type="usc_title"),
        dict(kind="rule", source_type="case_law"),
    ],
)
def test_render_citation_refuses_an_unknown_kind_or_source(kw):
    with pytest.raises(ValueError):
        render_citation(label="x", appendix=None, page_start=1, page_end=1, book=book, **kw)


def test_render_citation_refuses_backwards_pages():
    with pytest.raises(ValueError):
        render_citation(
            label="x", kind="rule", source_type="usc_title", appendix=None, page_start=9, page_end=8, book=book
        )


@pytest.mark.parametrize(
    "a,b,expected",
    [
        (20, 20, "p. 20"),
        (143, 144, "pp. 143–44"),
        (8, 9, "pp. 8–9"),
        (99, 101, "pp. 99–101"),
        (199, 201, "pp. 199–201"),
        (110, 112, "pp. 110–12"),
        (98, 99, "pp. 98–99"),
        (149, 150, "pp. 149–50"),  # only the last two digits differ in the sense of the rule: the hundreds match
        (5, 12, "pp. 5–12"),
        (1999, 2001, "pp. 1999–2001"),
    ],
)
def test_page_span(a, b, expected):
    assert page_span(a, b) == expected


def test_page_span_refuses_backwards_pages():
    with pytest.raises(ValueError):
        page_span(144, 143)


def test_render_response_forms():
    c = {"circ92-2025-12:usc17-s107": "CITE"}
    assert render_response("x (ref_id: circ92-2025-12:usc17-s107).", c) == "x (CITE)."
    assert render_response("x `ref_id: circ92-2025-12:usc17-s107`", c) == "x CITE"
    assert render_response("x ref_id: `circ92-2025-12:usc17-s107`.", c) == "x CITE."
    assert render_response("x circ92-2025-12:usc17-s107, y", c) == "x CITE, y"
    assert render_response("x ref_id: circ92-2025-12:usc17-s999", c) == "x [unverified reference: circ92-2025-12:usc17-s999]"


def test_the_sentence_period_survives_every_form():
    c = {"circ92-2025-12:usc17-s107": "CITE"}
    r = "circ92-2025-12:usc17-s107"
    assert render_response(f"See {r}.", c) == "See CITE."
    assert render_response(f"See `{r}`.", c) == "See CITE."
    assert render_response(f"See ref_id: {r}.", c) == "See CITE."
    assert render_response(f"See ref_id:{r}.", c) == "See CITE."
    assert render_response(f"See {r.replace('s107', 's999')}.", c) == "See [unverified reference: circ92-2025-12:usc17-s999]."


def test_render_response_handles_several_ids_and_leaves_other_text_alone():
    c = {"circ92-2025-12:usc17-s107": "A", "circ92-2025-12:ch1-n40": "B"}
    text = "One (ref_id: circ92-2025-12:usc17-s107); two `circ92-2025-12:ch1-n40`; three circ92-2025-12:usc17-s107."
    assert render_response(text, c) == "One (A); two B; three A."
    assert render_response("Nothing to cite here; see section 107.", c) == "Nothing to cite here; see section 107."
    assert render_response("", c) == ""


@pytest.mark.parametrize(
    "text,expected",
    [
        ("x ref_id: usc17-s107.", "x [unverified reference: usc17-s107]."),
        ("x (ref_id: circ92:usc17-s107)", "x ([unverified reference: circ92:usc17-s107])"),
        ("x `ref_id: usc17-s107`", "x [unverified reference: usc17-s107]"),
        ("x ref_id: `usc17-s107`.", "x [unverified reference: usc17-s107]."),
        (
            "x ref_id: circ92-2025-12:usc17:s107.",
            "x [unverified reference: circ92-2025-12:usc17:s107].",
        ),
    ],
)
def test_an_off_grammar_id_after_the_marker_renders_unverified(text, expected):
    assert render_response(text, {"circ92-2025-12:usc17-s107": "CITE"}) == expected


def test_a_bare_off_grammar_token_is_left_alone():
    assert render_response("see usc17-s107 and circ92:usc17-s107", {}) == "see usc17-s107 and circ92:usc17-s107"


def test_an_empty_citation_renders_as_unverified_not_as_nothing():
    assert render_response("x circ92-2025-12:usc17-s107.", {"circ92-2025-12:usc17-s107": ""}) == (
        "x [unverified reference: circ92-2025-12:usc17-s107]."
    )


def test_render_response_does_not_read_the_citation_as_a_template():
    c = {"circ92-2025-12:usc17-s107": r"17 U.S.C. §107 \1 \g<0> {ref_id}"}
    assert render_response("x circ92-2025-12:usc17-s107", c) == r"x 17 U.S.C. §107 \1 \g<0> {ref_id}"


def test_render_response_leaves_backticks_that_do_not_wrap_the_id():
    c = {"circ92-2025-12:usc17-s107": "CITE"}
    assert render_response("`see circ92-2025-12:usc17-s107`", c) == "`see CITE`"
    assert render_response("retrieved_ref_id: circ92-2025-12:usc17-s107", c) == "retrieved_ref_id: CITE"


@pytest.mark.parametrize(
    "text",
    [
        "Fair use (ref_id: circ92-2025-12:usc17-s107). See `circ92-2025-12:ch1-n40`, and circ92-2025-12:usc17-s101-def-joint-work.",
        "ref_id:circ92-2025-12:usc17-s107 and ref_id: `circ92-2025-12:ch1-n40`.",
        "`ref_id: circ92-2025-12:usc17-s107` then my-circ92-2025-12:usc17-s106 then x_circ92-2025-12:usc17-s108",
        "ref_id: usc17-s107, `ref_id: circ92:ch1-n40`, ref_id: `usc17-s106`. and ref_id:circ92-2025-12:usc17:s109",
    ],
)
def test_every_id_the_check_finds_is_one_the_renderer_replaces(text):
    ids = find_cited_ref_ids(text)
    assert ids
    out = render_response(text, {i: "X" for i in ids})
    assert find_cited_ref_ids(out) == []
    assert "ref_id:" not in out


def test_unverified_template():
    assert UNVERIFIED == "[unverified reference: {ref_id}]"


def test_find_stated_pages():
    assert find_stated_pages("see p. 12 and pp. 12–13, page 40") == ["p. 12", "pp. 12–13", "page 40"]
    assert find_stated_pages("Pub. L. No. 105-304; §512(p); 17 U.S.C. §107") == []


def test_find_stated_pages_forms():
    assert find_stated_pages("P. 7") == ["P. 7"]
    assert find_stated_pages("Pages 3-5 and page 9") == ["Pages 3-5", "page 9"]
    assert find_stated_pages("pp.12 - 14") == ["pp.12 - 14"]
    # not pages: an appendix label, a paragraph label, a note number
    assert find_stated_pages("app. B n.2, section 107(p)(3), ch. 1 n.3") == []
    assert find_stated_pages("see app. 12 and cap. 3") == []  # `p.` inside a longer word is not a page
    assert find_stated_pages("") == []
