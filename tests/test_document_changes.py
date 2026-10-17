"""scripts/document_changes.py — the verdicts and paragraph labels behind
feedback_report --changes (spec 2026-10-15, "Reading it"). Pure: no database."""
from __future__ import annotations

import time

import pytest

from scripts.document_changes import (
    ALTERED,
    KEPT,
    NO_LATER,
    REVERTED,
    build_document_changes,
    changed_paragraphs,
    edit_verdict,
    format_document_changes,
    normalize,
    paragraphs,
)

_REPLACE = {"action": "replace", "target_text": "the Seller", "new_text": "Acme Ltd"}
_INSERT = {"action": "insert", "target_text": "4. Term", "new_text": "4.1 Renewal is automatic."}
_DELETE = {"action": "delete", "target_text": "Non-solicitation for 24 months."}
_REDLINE = {"action": "redline", "target_text": "Cap: 1x fees\nExcluding fraud", "new_text": "Cap: 2x fees"}


@pytest.mark.parametrize("applied, final, verdict", [
    (_REPLACE, "Sold by Acme Ltd.", KEPT),
    (_REPLACE, "Sold by the Seller.", REVERTED),
    (_REPLACE, "Sold by Acme Limited.", ALTERED),
    ({**_REPLACE, "action": "replace_all", "target_text": "[__]"}, "Acme Ltd and [__]", KEPT),
    (_INSERT, "4. Term\r4.1 Renewal is automatic.", KEPT),
    (_INSERT, "4. Term\r4.1 Renewal needs notice.", ALTERED),
    (_DELETE, "Term. Fees.", KEPT),
    (_DELETE, "Non-solicitation for 24 months. Fees.", REVERTED),
    (_REDLINE, "Cap: 2x fees", KEPT),
    (_REDLINE, "Cap: 1x fees\rExcluding fraud", REVERTED),
    (_REDLINE, "Cap: 3x fees\rExcluding fraud", ALTERED),
    ({"action": "replace", "target_text": "Optional clause.", "new_text": ""}, "Fees.", KEPT),
])
def test_each_verdict(applied, final, verdict):
    """The spec's verdict table, a case per cell. Mutation: swap kept and reverted."""
    assert edit_verdict(applied, final) == verdict


_CONSENT = {"action": "replace", "target_text": "consent, not to be unreasonably withheld",
            "new_text": "consent"}


@pytest.mark.parametrize("final, verdict", [
    ("Assignment needs the consent, not to be unreasonably withheld.", REVERTED),
    ("Assignment needs the consent.", KEPT),
    ("Assignment needs the approval.", ALTERED),
], ids=["target-back", "new-only", "neither"])
def test_a_replacement_whose_new_text_sits_inside_its_target(final, verdict):
    """The new text is part of the old, so the old wording being back is the only
    sign the edit was rejected. Mutation: drop the new-inside-target branch (the
    rejected edit then reads kept)."""
    assert edit_verdict(_CONSENT, final) == verdict


# Clause 7 as the document words it, and as the model quoted it. deleteClause (word.ts)
# has no completeness check, so Word deletes the real clause from the misquote.
_CLAUSE_7 = ("7. Non-solicitation. For a period of 24 months, neither party shall "
             "solicit the other's staff.")
_WITH_7 = f"6. Fees.\r{_CLAUSE_7}\r8. Law."
_WITHOUT_7 = "6. Fees.\r8. Law."
_MISQUOTED = {"action": "delete", "target_text":
              "7. Non-solicitation. For 24 months, neither party shall solicit the other's staff."}
_QUOTED = {"action": "delete", "target_text": _CLAUSE_7}
_EMPTIED = {"action": "replace", "new_text": ""}


@pytest.mark.parametrize("applied, final, verdict", [
    (_MISQUOTED, _WITH_7, ALTERED),
    (_MISQUOTED, _WITHOUT_7, ALTERED),
    (_QUOTED, _WITHOUT_7, KEPT),
    (_QUOTED, _WITH_7, REVERTED),
    ({**_MISQUOTED, **_EMPTIED}, _WITH_7, ALTERED),
    ({**_MISQUOTED, **_EMPTIED}, _WITHOUT_7, ALTERED),
    ({**_QUOTED, **_EMPTIED}, _WITHOUT_7, KEPT),
    ({**_QUOTED, **_EMPTIED}, _WITH_7, REVERTED),
    ({**_MISQUOTED, **_EMPTIED, "action": "replace_all"}, _WITH_7, ALTERED),
], ids=["misquoted-delete-rejected", "misquoted-delete-kept", "delete-kept", "delete-rejected",
        "misquoted-emptying-rejected", "misquoted-emptying-kept", "emptying-kept",
        "emptying-rejected", "misquoted-emptying-replace-all"])
def test_a_removal_whose_target_was_not_in_the_text_before_reads_altered(applied, final, verdict):
    """A delete, or a replacement with nothing, is judged by its target's absence —
    which proves nothing when the target was not word-for-word in the text before the
    edit: the misquote is absent whether Word's deletion was kept or rejected.
    Mutation: drop the before-text check (both misquoted removals then read kept)."""
    assert edit_verdict(applied, final, before_text=_WITH_7) == verdict


# The document says "4. Term"; the model's context line says "4 Term". Word applies only
# the line that differs (simplifyMultilineReplace), so the context line is never searched.
_TERM = {"action": "replace", "target_text": "4 Term\nThis Agreement lasts 12 months.",
         "new_text": "4 Term\nThis Agreement lasts 24 months."}
_TERM_KEPT = "4. Term\rThis Agreement lasts 24 months.\r5. Fees."
_TERM_REJECTED = "4. Term\rThis Agreement lasts 12 months.\r5. Fees."


@pytest.mark.parametrize("applied, final, verdict", [
    (_TERM, _TERM_KEPT, KEPT),
    (_TERM, _TERM_REJECTED, REVERTED),
    ({**_TERM, "new_text": "4 Term \nThis Agreement lasts 24 months."}, _TERM_KEPT, KEPT),
    ({**_TERM, "target_text": "4 Term\nconsent, not to be unreasonably withheld",
      "new_text": "4 Term\nconsent"}, "4. Term\rconsent, not to be unreasonably withheld", REVERTED),
    ({**_TERM, "new_text": "4. Term\nThis Agreement lasts 24 months."}, _TERM_REJECTED, ALTERED),
    ({**_TERM, "target_text": "4 Term\nThis Agreement lasts 12 months.\nIt renews yearly."},
     _TERM_KEPT, ALTERED),
    ({**_TERM, "action": "replace_all"}, _TERM_KEPT, ALTERED),
], ids=["kept", "rejected", "context-differs-only-in-spacing", "shortening-pair-rejected",
        "two-lines-differ", "unequal-line-counts", "replace-all-is-never-narrowed"])
def test_a_multi_line_replace_is_judged_on_the_one_line_word_applied(applied, final, verdict):
    """With as many lines on each side and exactly one differing after trimming, a
    `replace` is judged on that line pair, so the shortening rule applies to the pair
    too. Otherwise — two lines differ, the counts differ, or it is a `replace_all` —
    Word replaces the whole text, and the whole text is judged. Mutations: judge the
    whole text always (kept and rejected both read altered); narrow on the first of
    several differing lines; drop the equal-count or the trimming condition; narrow
    replace_all too."""
    assert edit_verdict(applied, final) == verdict


def test_a_curled_quote_or_a_rewrapped_line_is_not_an_alteration():
    """Word curls straight quotes as you type. Mutation: compare without normalize()."""
    applied = {"action": "replace", "target_text": "Buyer", "new_text": 'the "Client" shall\npay'}
    assert edit_verdict(applied, "Then the \u201cClient\u201d shall pay within 30 days.") == KEPT
    assert normalize("a\u00a0b\u202fc") == "a b c"


def test_paragraphs_split_on_words_carriage_returns():
    """Review Focus 3: Word separates paragraphs with \\r. Mutation: split("\\n")."""
    assert paragraphs("1. Parties\r2. Term\r\n3. Fees\n\n  \n4. Law") == [
        "1. Parties", "2. Term", "3. Fees", "4. Law"]


def test_empty_texts_have_no_paragraphs_and_change_nothing():
    """Review Focus 4."""
    assert paragraphs("") == [] and changed_paragraphs("", "", []) == []


def test_changed_paragraphs_are_labelled_side_by_side():
    """A clause typed where an applied deletion was is the attorney's, not the
    assistant's. Mutations: label everything one way; pair old with new."""
    first = "1. Parties: [__]\r2. Term: 1 year\r3. Non-solicit: 24 months"
    last = "1. Parties: Acme Ltd\r2. Term: 2 years\r4. Audit rights on request"
    applied = [{"action": "replace", "target_text": "[__]", "new_text": "Acme Ltd"},
               {"action": "delete", "target_text": "3. Non-solicit: 24 months"}]
    assert changed_paragraphs(first, last, applied) == [
        {"mark": "-", "text": "1. Parties: [__]", "label": "assistant"},
        {"mark": "-", "text": "2. Term: 1 year", "label": "not suggested"},
        {"mark": "-", "text": "3. Non-solicit: 24 months", "label": "assistant"},
        {"mark": "+", "text": "1. Parties: Acme Ltd", "label": "assistant"},
        {"mark": "+", "text": "2. Term: 2 years", "label": "not suggested"},
        {"mark": "+", "text": "4. Audit rights on request", "label": "not suggested"},
    ]


def test_each_line_of_a_multi_line_insert_is_labelled_as_the_assistants():
    """A two-line new_text is one paragraph per line in the document, so each `+`
    holds an applied paragraph. Mutation: hold the whole applied text as one string."""
    first = "3. Fees\r4. Term"
    last = "3. Fees\r4. Term\r4.1 Renewal is automatic.\r4.2 Notice is 30 days."
    insert = {"action": "insert", "target_text": "4. Term",
              "new_text": "4.1 Renewal is automatic.\n4.2 Notice is 30 days."}
    assert changed_paragraphs(first, last, [insert]) == [
        {"mark": "+", "text": "4.1 Renewal is automatic.", "label": "assistant"},
        {"mark": "+", "text": "4.2 Notice is 30 days.", "label": "assistant"},
    ]


def test_each_paragraph_of_a_multi_line_deletion_is_labelled_as_the_assistants():
    """A two-paragraph clause removed by one edit: each `-` holds an applied
    paragraph. Mutation: hold the whole applied target as one string."""
    first = "2. Term\r3. Non-solicit: 24 months\r4. Exclusivity: 12 months\r5. Fees"
    last = "2. Term\r5. Fees"
    delete = {"action": "delete",
              "target_text": "3. Non-solicit: 24 months\n4. Exclusivity: 12 months"}
    assert changed_paragraphs(first, last, [delete]) == [
        {"mark": "-", "text": "3. Non-solicit: 24 months", "label": "assistant"},
        {"mark": "-", "text": "4. Exclusivity: 12 months", "label": "assistant"},
    ]


def test_an_edits_unchanged_context_lines_explain_no_paragraph():
    """The pane records the card's full strings, but Word applies only the one changed
    line of a multi-line replace (simplifyMultilineReplace), so a line the target and
    new_text share is context, not change. "4. Term" is a substring of the attorney's
    own "14. Termination…" and must not claim it. Mutation: put every line of target_text
    and new_text in the explained sets (both Termination rows then read `assistant`)."""
    first = ("4. Term\r"
             "This Agreement lasts 12 months.\r"
             "14. Termination for convenience: either party may terminate on 30 days' notice.")
    last = ("4. Term\r"
            "This Agreement lasts 24 months.\r"
            "14. Termination for convenience: either party may terminate on 60 days' written notice.")
    edit = {"action": "replace",
            "target_text": "4. Term\nThis Agreement lasts 12 months.",
            "new_text": "4. Term\nThis Agreement lasts 24 months."}
    assert changed_paragraphs(first, last, [edit]) == [
        {"mark": "-", "text": "This Agreement lasts 12 months.", "label": "assistant"},
        {"mark": "-", "label": "not suggested",
         "text": "14. Termination for convenience: either party may terminate on 30 days' notice."},
        {"mark": "+", "text": "This Agreement lasts 24 months.", "label": "assistant"},
        {"mark": "+", "label": "not suggested",
         "text": "14. Termination for convenience: either party may terminate on 60 days' written notice."},
    ]


def test_an_insert_never_takes_its_anchor_out_and_owns_every_line_it_puts_in():
    """An insert's target is only its anchor, never text the insert changes. So the
    anchor explains no `-` paragraph, and the shared-line rule is not applied to inserts:
    a new_text line equal to the anchor is a real new paragraph. Mutations: put the
    anchor in the taken-out lines (the hand-deleted "14. Termination…" row then reads
    `assistant`); apply the shared-line rule to inserts (the repeated `+ 4. Term` then
    reads `not suggested`)."""
    first = "3. Fees\r4. Term\r14. Termination for convenience: 30 days' notice."
    last = "3. Fees\r4. Term\r4. Term\r4.1 Renewal is automatic."
    insert = {"action": "insert", "target_text": "4. Term",
              "new_text": "4. Term\n4.1 Renewal is automatic."}
    assert changed_paragraphs(first, last, [insert]) == [
        {"mark": "-", "text": "14. Termination for convenience: 30 days' notice.",
         "label": "not suggested"},
        {"mark": "+", "text": "4. Term", "label": "assistant"},
        {"mark": "+", "text": "4.1 Renewal is automatic.", "label": "assistant"},
    ]


def _obs(ts, sha, kind="idle", **over):
    o = {"timestamp": ts, "kind": kind, "sha256": sha, "attorney_id": "atty-1", "user_name": "",
         "tracked_changes": None if kind == "turn" else [], "tracked_total": 0}
    o.update(over)
    return o


A, B = "a" * 64, "b" * 64
_VERSIONS = {A: {"text": "Fees: 10 days.", "truncated": False},
             B: {"text": "Fees: 30 days.", "truncated": False}}
_EDIT = {"action": "replace", "target_text": "10 days", "new_text": "30 days"}


def _applied(edit, ts="2026-10-16T09:01:00+00:00", turn_id="t-1"):
    return {"timestamp": ts, "turn_id": turn_id, "applied": edit}


def test_the_last_sighting_decides_even_when_its_text_was_seen_before():
    """A → B → A: the edit to B was undone, though A's version is the older one."""
    d = build_document_changes("doc-1", [
        _obs("2026-10-16T09:00:00+00:00", A, kind="turn"),
        _obs("2026-10-16T09:05:00+00:00", B),
        _obs("2026-10-16T09:09:00+00:00", A),
    ], _VERSIONS, [_applied(_EDIT)])
    assert [e["verdict"] for e in d["edits"]] == [REVERTED]


def test_an_edit_after_the_last_sighting_has_no_later_capture():
    """Mutation: judge against the last sighting regardless of time."""
    d = build_document_changes("doc-1", [_obs("2026-10-16T09:00:00+00:00", A, kind="turn")],
                               _VERSIONS, [_applied(_EDIT)])
    assert d["edits"][0]["verdict"] == NO_LATER


C, D = "c" * 64, "d" * 64


def test_a_removal_is_judged_against_the_last_text_stored_before_it():
    """The baseline is the last stored text before the Apply — here the proposing
    turn's own. Neither the older draft (clause 7 still said 12 months) nor the later
    text (the clause gone) is. Mutations: take the first stored text; take the last
    stored text regardless of time (either way the kept deletion reads altered)."""
    versions = {A: {"text": _WITH_7.replace("a period of 24", "a period of 12"), "truncated": False},
                B: {"text": _WITH_7, "truncated": False},
                C: {"text": _WITHOUT_7, "truncated": False}}
    d = build_document_changes("doc-1", [
        _obs("2026-10-16T08:00:00+00:00", A, kind="turn"),
        _obs("2026-10-16T09:00:00+00:00", B, kind="turn"),
        _obs("2026-10-16T09:05:00+00:00", C),
    ], versions, [_applied(_QUOTED)])
    assert d["edits"][0]["verdict"] == KEPT


def test_a_sighting_without_a_stored_text_is_no_baseline():
    """A turn whose version write failed has only a fingerprint, so the baseline is
    the stored text before it: clause 7 as quoted was there, the misquote never was.
    Mutation: take the last observation of any kind before the Apply — with no text,
    it either reads as nothing stored (the misquote then reads kept) or as an empty
    text (the quoted removal then reads altered)."""
    versions = {B: {"text": _WITH_7, "truncated": False},
                C: {"text": _WITHOUT_7, "truncated": False}}
    d = build_document_changes("doc-1", [
        _obs("2026-10-16T09:00:00+00:00", B, kind="turn"),
        _obs("2026-10-16T09:00:30+00:00", D, kind="turn"),
        _obs("2026-10-16T09:05:00+00:00", C),
    ], versions, [_applied(_QUOTED), _applied(_MISQUOTED, ts="2026-10-16T09:02:00+00:00")])
    assert [e["verdict"] for e in d["edits"]] == [KEPT, ALTERED]


def test_a_removal_with_no_text_stored_before_it_keeps_the_tables_verdict():
    """Turns from before SP4 have only a fingerprint. With no stored text from before
    the edit, the verdict table decides: a misquoted deletion reads kept, as it did
    before the baseline existed. Mutation: treat "nothing stored before" as an empty
    text (the deletion then reads altered)."""
    d = build_document_changes("doc-1", [
        _obs("2026-10-16T08:00:00+00:00", D, kind="turn"),
        _obs("2026-10-16T09:05:00+00:00", C),
    ], {C: {"text": _WITH_7, "truncated": False}}, [_applied(_MISQUOTED)])
    assert d["edits"][0]["verdict"] == KEPT


def test_sightings_without_a_stored_text_are_counted_not_compared():
    """Turns from before SP4 have a fingerprint and no text; an empty view has
    neither (Review Focus 4)."""
    d = build_document_changes("doc-1", [
        _obs("2026-10-16T08:00:00+00:00", "c" * 64, kind="turn"),
        _obs("2026-10-16T08:30:00+00:00", ""),
        _obs("2026-10-16T09:00:00+00:00", A, kind="turn"),
    ], _VERSIONS, [])
    assert d["first_stored"]["timestamp"] == "2026-10-16T09:00:00+00:00" and d["skipped"] == 2
    assert d["distinct"] == 2


def test_an_altered_edit_shows_the_closest_paragraph_now():
    versions = {
        A: {"text": "Liability cap: 1x fees.\rGoverning law: England.", "truncated": False},
        B: {"text": "Liability cap: the fees paid in the 6 months preceding the claim.\r"
                    "Governing law: England.", "truncated": False},
    }
    edit = {"action": "replace", "target_text": "1x fees",
            "new_text": "the fees paid in the 12 months preceding the claim"}
    d = build_document_changes("doc-1", [_obs("2026-10-16T09:00:00+00:00", A, kind="turn"),
                                         _obs("2026-10-16T09:05:00+00:00", B)],
                               versions, [_applied(edit, turn_id="")])
    assert d["edits"][0]["verdict"] == ALTERED
    assert d["edits"][0]["closest"] == \
        "Liability cap: the fees paid in the 6 months preceding the claim."


def test_an_altered_edit_with_no_shared_word_has_no_closest_paragraph():
    """A paragraph sharing no word with the edit is not "the closest": the closest
    is "". Mutation: drop the zero-overlap check (max then returns the first paragraph)."""
    versions = {
        A: {"text": "Fees: 10 days.", "truncated": False},
        B: {"text": "NON-DISCLOSURE AGREEMENT\rGoverning law: England.", "truncated": False},
    }
    edit = {"action": "replace", "target_text": "the Seller", "new_text": "Acme Ltd"}
    d = build_document_changes("doc-1", [_obs("2026-10-16T09:00:00+00:00", A, kind="turn"),
                                         _obs("2026-10-16T09:05:00+00:00", B)],
                               versions, [_applied(edit, turn_id="")])
    assert d["edits"][0]["verdict"] == ALTERED
    assert d["edits"][0]["closest"] == ""


def test_the_block_reads_as_the_spec_shows_it():
    tracked = [{"type": "Added", "author": "Jane Smith", "text": "30"},
               {"type": "Deleted", "author": "Jane Smith", "text": "10"},
               {"type": "Added", "author": "Counterparty LLP", "text": "x"}]
    d = build_document_changes("doc-1", [
        _obs("2026-10-16T09:00:00+00:00", A, kind="turn", user_name="A. Tester"),
        _obs("2026-10-16T09:05:00+00:00", B, tracked_changes=tracked, tracked_total=3),
    ], _VERSIONS, [_applied({**_EDIT, "proposed_text": "20 days"}, turn_id="6aef53da-0000")])
    out = "\n".join(format_document_changes(d))
    assert "document doc-1  A. Tester (atty-1) · 2026-10-16 09:00 → 2026-10-16 09:05" in out
    assert ("observations 2 (1 turns · 0 pane_open · 1 idle · 0 finalize) · 2 distinct texts"
            in out)
    assert "first stored text 2026-10-16 09:00 (turn)" in out
    assert "applied edits 1: 1 kept · 0 altered · 0 reverted · 0 no later capture" in out
    assert (f'{"kept":<17}{"replace":<12}"10 days" → "30 days"   (edited before applying)'
            "   turn 6aef53da") in out
    assert ("tracked changes at the last capture: 3 — Jane Smith 2 (1 ins, 1 del) · "
            "Counterparty LLP 1 (1 ins)") in out
    assert ("paragraphs changed since the first text: 2 — 2 explained by an applied edit · "
            "0 not suggested") in out


def test_unreadable_or_absent_tracked_changes_say_so():
    turn_only = build_document_changes(
        "doc-1", [_obs("2026-10-16T09:00:00+00:00", A, kind="turn")], _VERSIONS, [])
    unreadable = build_document_changes(
        "doc-1", [_obs("2026-10-16T09:00:00+00:00", A, tracked_total=-1)], _VERSIONS, [])
    assert "tracked changes: no pane capture yet" in "\n".join(format_document_changes(turn_only))
    assert "tracked changes at the last capture: unreadable in that Word" in \
        "\n".join(format_document_changes(unreadable))


def test_excerpts_are_cut_unless_full():
    d = build_document_changes(
        "doc-1", [_obs("2026-10-16T09:00:00+00:00", A, kind="turn"),
                  _obs("2026-10-16T09:05:00+00:00", B)],
        _VERSIONS, [_applied({"action": "delete", "target_text": "x" * 100}, turn_id="")])
    short = "\n".join(format_document_changes(d))
    full = "\n".join(format_document_changes(d, full=True))
    assert "x" * 39 + "…" in short and "x" * 100 not in short and "x" * 100 in full


def test_a_line_break_in_an_applied_text_does_not_break_its_row():
    """A line break inside an applied text would split its row on a terminal.
    Mutation: format the raw target_text and new_text."""
    redline = {"action": "redline", "target_text": "Cap: 1x fees\nExcluding fraud",
               "new_text": "Cap: 2x fees\r\nExcluding fraud"}
    d = build_document_changes("doc-1", [_obs("2026-10-16T09:00:00+00:00", A, kind="turn"),
                                         _obs("2026-10-16T09:05:00+00:00", B)],
                               _VERSIONS, [_applied(redline, turn_id="")])
    for full in (False, True):
        lines = format_document_changes(d, full=full)
        assert not any("\n" in line or "\r" in line for line in lines)
        assert any('"Cap: 1x fees Excluding fraud"' in line for line in lines)
        assert any('"Cap: 2x fees Excluding fraud"' in line for line in lines)


def test_a_long_negotiated_contract_reads_back_in_seconds():
    """Review Focus 5: 2,000 paragraphs, 100 applied edits, 60 reworded after.
    Mutation: rank every paragraph with difflib's ratio for the closest one."""
    paras = [f"{i}. The Supplier shall perform obligation number {i} with due care and skill."
             for i in range(2000)]
    last_paras = list(paras)
    edits = []
    for n, i in enumerate(range(0, 2000, 20)):
        new = f"{i}. The Supplier shall perform obligation number {i} within ten days."
        edits.append(_applied({"action": "replace", "target_text": paras[i], "new_text": new},
                              turn_id=""))
        last_paras[i] = new.replace("ten", "fifteen") if n < 60 else new
    versions = {A: {"text": "\r".join(paras), "truncated": False},
                B: {"text": "\r".join(last_paras), "truncated": False}}
    started = time.monotonic()
    d = build_document_changes("doc-1", [_obs("2026-10-16T09:00:00+00:00", A, kind="turn"),
                                         _obs("2026-10-16T09:05:00+00:00", B)], versions, edits)
    lines = format_document_changes(d)
    assert time.monotonic() - started < 1
    assert sum(e["verdict"] == ALTERED for e in d["edits"]) == 60
    assert "    … 180 more — --full lists them all" in lines
