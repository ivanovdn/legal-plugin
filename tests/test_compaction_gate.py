"""The compaction validation gate — a deterministic, zero-LLM guard on an LLM's output.

Every quote carries the conversation_store row id it came from, so a fabricated
line is not merely detectable, it is REJECTABLE — a summary is legal recall, and
one invented line in it is worse than no summary at all.

Two layers, tested here in order. `validate_segment` is strict: any failing quote
invalidates the whole body. `partition_quotes` is what production calls — it keeps
every line that verifies, drops the ones that do not, and is fatal only when NOTHING
verifies. Both share `_quote_failure`, so the lenient path can never accept a line
the strict path would reject.

The gate is also scored as a corpus: `evals/cases/gate-*.json`, run by
`evals/run_gate.py`. These tests assert the code's behaviour; the corpus prices the
trade-off every change to the boundary rules makes between fabrications rejected and
ordinary quotes still accepted.
"""
from skills.legal_research.compaction import (
    parse_quote_lines,
    partition_quotes,
    render_segment,
    validate_segment,
)

ROWS = [
    {"id": 412, "role": "user", "content": "Please use Suzy Quatro for all signature blocks."},
    {"id": 413, "role": "assistant", "content": "Done — the cap is Green under the playbook."},
    {"id": 414, "role": "user", "content": "We'll accept 12 months."},
]


def test_parses_both_speaker_forms():
    quotes, err = parse_quote_lines(
        '[#412 attorney] "use Suzy Quatro"\n'
        '[#413 assistant, said earlier] "the cap is Green"'
    )
    assert err == ""
    assert quotes == [
        {"row_id": 412, "speaker": "attorney", "text": "use Suzy Quatro"},
        {"row_id": 413, "speaker": "assistant", "text": "the cap is Green"},
    ]


def test_strips_a_code_fence_the_model_added():
    quotes, err = parse_quote_lines('```\n[#412 attorney] "use Suzy Quatro"\n```')
    assert err == ""
    assert [q["row_id"] for q in quotes] == [412]


def test_prose_alongside_quotes_is_rejected():
    # Unparsed prose would ride into the prompt unvalidated — the whole point of
    # the format is that EVERY line is checkable.
    _, err = parse_quote_lines(
        'Here is a summary of the conversation:\n[#412 attorney] "use Suzy Quatro"'
    )
    assert "not a quote line" in err


def test_empty_body_is_rejected():
    _, err = parse_quote_lines("   \n\n  ")
    assert "no quote lines" in err


def test_valid_segment_passes():
    body = (
        '[#412 attorney] "Please use Suzy Quatro for all signature blocks"\n'
        '[#413 assistant, said earlier] "Done — the cap is Green under the playbook"\n'
        '[#414 attorney] "We\'ll accept 12 months"'
    )
    assert validate_segment(body, ROWS, 412, 414) == ""


def test_quote_citing_a_row_outside_the_range_is_rejected():
    body = '[#999 attorney] "use Suzy Quatro"'
    err = validate_segment(body, ROWS, 412, 414)
    assert "outside the condensed range" in err


def test_quote_citing_a_row_not_in_the_transcript_is_rejected():
    # In range but absent from rows: the row was dropped by _sanitize_history
    # because it was pure machinery, so nothing can vouch for this quote.
    rows = [r for r in ROWS if r["id"] != 413]
    body = '[#413 assistant, said earlier] "the cap is Green"'
    err = validate_segment(body, rows, 412, 414)
    assert "not in the condensed transcript" in err


def test_misquoted_text_is_rejected():
    body = '[#414 attorney] "we will accept 24 months"'
    err = validate_segment(body, ROWS, 412, 414)
    assert "does not appear" in err


def test_one_bad_quote_invalidates_the_whole_segment():
    body = (
        '[#412 attorney] "Please use Suzy Quatro for all signature blocks"\n'
        '[#414 attorney] "we will accept 24 months"'
    )
    assert validate_segment(body, ROWS, 412, 414) != ""


def test_wrong_speaker_label_is_rejected():
    # Row 413 is the assistant. Attributing its words to the attorney is exactly
    # the "asserts something the attorney never said" risk, and free to catch.
    body = '[#413 attorney] "Done — the cap is Green under the playbook"'
    err = validate_segment(body, ROWS, 412, 414)
    assert "labelled attorney" in err


def test_normalisation_tolerates_curly_quotes_and_whitespace():
    rows = [{"id": 5, "role": "user", "content": "The parties’ cap is  12 months."}]
    body = '[#5 attorney] "The parties\' cap is 12 months."'
    assert validate_segment(body, rows, 5, 5) == ""


def test_empty_quote_text_is_rejected():
    body = '[#412 attorney] ""'
    assert "is empty" in validate_segment(body, ROWS, 412, 414)


def test_render_segment_adds_the_header_and_precedence_note():
    quotes = [{"row_id": 412, "speaker": "attorney", "text": "use Suzy Quatro"}]
    block = render_segment(quotes, message_count=18)
    assert block.startswith("--- EARLIER IN THIS CONVERSATION (18 earlier messages, condensed) ---")
    # The block must rank itself BELOW live grounding in its own words: recalled
    # discussion that reads as a current finding is the residual risk the gate
    # cannot catch.
    assert "take precedence over anything here" in block
    assert '[#412 attorney] "use Suzy Quatro"' in block
    assert block.rstrip().endswith("--- END EARLIER IN THIS CONVERSATION ---")


def test_render_segment_marks_assistant_lines_as_said_earlier():
    quotes = [{"row_id": 413, "speaker": "assistant", "text": "the cap is Green"}]
    # A position the document has since outgrown must read as history, not as a
    # live legal judgment.
    assert '[#413 assistant, said earlier] "the cap is Green"' in render_segment(quotes, 2)


def test_a_fragment_that_drops_a_leading_negation_is_rejected():
    # "accept 12 months" is a genuine contiguous substring of the row, yet it asserts
    # the OPPOSITE of what the attorney said. A fabrication assembled entirely from
    # real characters is exactly what this gate must catch.
    rows = [{"id": 7, "role": "user", "content": "We will not accept 12 months."}]
    err = validate_segment('[#7 attorney] "accept 12 months"', rows, 7, 7)
    assert "not a complete sentence" in err


def test_a_prefix_that_drops_a_trailing_condition_is_rejected():
    # The mirror image: a genuine prefix that silently drops the condition the
    # acceptance depended on.
    rows = [{
        "id": 8, "role": "user",
        "content": "We will accept 12 months only if the cap is raised.",
    }]
    err = validate_segment('[#8 attorney] "We will accept 12 months"', rows, 8, 8)
    assert "not a complete sentence" in err


def test_a_complete_sentence_from_the_middle_of_a_message_is_accepted():
    # Sentence-aligned at both ends, so the trim cannot have inverted the meaning.
    # This is what the gate must keep ALLOWING — a rule that only rejects is a rule
    # that kills the feature.
    rows = [{
        "id": 9, "role": "assistant",
        "content": (
            "I checked the playbook. The cap is Green under our standard position. "
            "Escalation is not required."
        ),
    }]
    assert validate_segment(
        '[#9 assistant, said earlier] "The cap is Green under our standard position"',
        rows, 9, 9,
    ) == ""


def test_a_colon_does_not_make_a_quotable_boundary():
    # A colon SUBORDINATES the condition that follows it. Treating it as a sentence
    # boundary would let the same meaning-inverting chop back in through different
    # punctuation — which is exactly how the first fix for this failed.
    rows = [{
        "id": 10, "role": "user",
        "content": "We will accept 12 months: only if the cap is raised.",
    }]
    err = validate_segment('[#10 attorney] "We will accept 12 months"', rows, 10, 10)
    assert "not a complete sentence" in err


def test_a_semicolon_does_not_make_a_quotable_boundary():
    rows = [{
        "id": 11, "role": "user",
        "content": "We will accept 12 months; but only for the first year.",
    }]
    err = validate_segment('[#11 attorney] "We will accept 12 months"', rows, 11, 11)
    assert "not a complete sentence" in err


def test_a_quote_may_not_END_on_a_colon_or_semicolon():
    """These two are what actually pin ';' and ':' out of _CLAUSE_END.

    The three tests around this one look like they cover it and do not: a quote that
    stops just BEFORE the punctuation is rejected by a different rule — only a full
    stop may be dropped from a quote's end — so they stay green even with ';:' added
    to _CLAUSE_END. Verified by mutation: with ';:' admitted the whole file passed.
    The exclusion only bites when the quote INCLUDES the punctuation, which is this
    shape, and a quote ending on a colon is a truncation like any other: it drops the
    condition the statement depended on while looking terminated.
    """
    rows = [{
        "id": 18, "role": "user",
        "content": "We can accept 12 months: Provided the cap is raised to 2x.",
    }]
    err = validate_segment('[#18 attorney] "We can accept 12 months:"', rows, 18, 18)
    assert "not a complete sentence" in err

    rows = [{
        "id": 19, "role": "user",
        "content": "We will accept 12 months; However, only for the first year.",
    }]
    err = validate_segment('[#19 attorney] "We will accept 12 months;"', rows, 19, 19)
    assert "not a complete sentence" in err


def test_a_hedge_before_a_colon_cannot_be_dropped():
    # The mirror image: the hedge sits BEFORE the colon, so quoting what follows it
    # strips the condition the statement depended on.
    rows = [{
        "id": 12, "role": "user",
        "content": "Assume nothing is agreed: we will accept 12 months.",
    }]
    err = validate_segment('[#12 attorney] "we will accept 12 months"', rows, 12, 12)
    assert "not a complete sentence" in err


def test_an_unpunctuated_message_is_quotable_only_in_full():
    # No terminal punctuation means no boundaries to trust, so only the whole message
    # can be quoted. Restrictive on purpose — any trim could be dropping a
    # qualification.
    rows = [{"id": 13, "role": "user", "content": "use Suzy for all signature blocks"}]
    assert validate_segment(
        '[#13 attorney] "use Suzy for all signature blocks"', rows, 13, 13
    ) == ""
    assert "not a complete sentence" in validate_segment(
        '[#13 attorney] "for all signature blocks"', rows, 13, 13
    )


def test_a_decimal_point_is_not_a_sentence_boundary():
    # "12.5" is one number, not the end of a statement. Reading its period as a full
    # stop lets a quote stop mid-sentence and drop the condition that followed.
    rows = [{
        "id": 14, "role": "user",
        "content": "We will accept 12.5 months only if the cap is raised.",
    }]
    err = validate_segment('[#14 attorney] "We will accept 12"', rows, 14, 14)
    assert "not a complete sentence" in err


def test_an_abbreviation_period_is_not_a_sentence_boundary():
    # Legal text is dense with these — Inc., Ltd., No., e.g. — so this is the ordinary
    # case rather than an exotic one.
    rows = [{
        "id": 15, "role": "user",
        "content": "We will accept the terms from Acme Inc. only if the cap is raised.",
    }]
    err = validate_segment(
        '[#15 attorney] "We will accept the terms from Acme Inc"', rows, 15, 15
    )
    assert "not a complete sentence" in err


def test_a_whole_sentence_containing_a_decimal_is_accepted():
    # The flip side: a decimal inside a quote must not block a legitimate whole
    # sentence, or the rule would reject most numeric commercial terms.
    rows = [{
        "id": 16, "role": "user",
        "content": "We will accept 12.5 months only if the cap is raised.",
    }]
    assert validate_segment(
        '[#16 attorney] "We will accept 12.5 months only if the cap is raised"',
        rows, 16, 16,
    ) == ""


def test_a_sentence_following_an_abbreviation_is_accepted():
    rows = [{
        "id": 17, "role": "assistant",
        "content": "Acme Inc. is the counterparty. The cap is Green.",
    }]
    assert validate_segment(
        '[#17 assistant, said earlier] "The cap is Green"', rows, 17, 17
    ) == ""


def test_matching_stays_case_insensitive():
    # Case is preserved for BOUNDARY detection only. A quote must still match its row
    # when the model reflows capitalisation, which it routinely does.
    rows = [{"id": 18, "role": "user", "content": "We will accept 12 months."}]
    assert validate_segment(
        '[#18 attorney] "we WILL accept 12 MONTHS"', rows, 18, 18
    ) == ""


def test_a_comma_qualifier_cannot_be_dropped():
    # A comma subordinates just as a colon does; only a full stop separates.
    rows = [{
        "id": 19, "role": "user",
        "content": "We will accept 12 months, subject to the cap being raised.",
    }]
    err = validate_segment('[#19 attorney] "We will accept 12 months"', rows, 19, 19)
    assert "not a complete sentence" in err


# --- Known residual -------------------------------------------------------------
# These pin the ONE truncation shape the gate does not catch, so it stays visible and
# cannot quietly widen. They assert today's behaviour on purpose: if someone tightens
# the rule later, these fail and force a conscious decision plus a docs update.
#
# Closing it was measured and rejected — every candidate rule also rejected sentences
# ending "…the MSA." or "I checked it.", and a gate that rejects ordinary text writes
# nothing at all. See the module docstring in skills/legal_research/compaction.py.


# The gate's one known defect — an unlisted abbreviation before a capitalised name
# ("…from Acme Ltd. Partners only if…") — is NOT pinned here. It lives in the eval
# corpus as `gate-residual-company-suffix-before-a-capitalised-name`, baselined in
# evals/baseline.json. A test can only pin a defect by asserting the buggy output is
# correct, which makes FIXING it look like a regression; the eval baseline expects the
# correct behaviour instead and reports `[now-passing]` when someone closes it. The
# other half of that trade — the legitimate quotes a wider abbreviation list would
# break — is priced by the case below and by
# `gate-accept-sentence-final-company-suffixes`.


def test_a_citation_abbreviation_is_not_a_sentence_boundary():
    # "v." introduces a case name, so the capital after it is not a new sentence. Before
    # this was closed, a quote could stop at "Smith v" and drop the condition entirely.
    rows = [{
        "id": 20, "role": "user",
        "content": "We will accept the terms of Smith v. Jones only if the cap is raised.",
    }]
    err = validate_segment(
        '[#20 attorney] "We will accept the terms of Smith v"', rows, 20, 20
    )
    assert "not a complete sentence" in err


def test_a_title_abbreviation_is_not_a_sentence_boundary():
    rows = [{
        "id": 21, "role": "user",
        "content": "We will accept the offer from Mr. Smith only if signed by Friday.",
    }]
    err = validate_segment(
        '[#21 attorney] "We will accept the offer from Mr"', rows, 21, 21
    )
    assert "not a complete sentence" in err


def test_the_domain_common_sentences_the_residual_protects_still_pass():
    # The other half of the trade, pinned so it cannot be lost silently. A rule closing
    # the two tests above would reject BOTH of these, and these are ordinary here —
    # MSA/NDA/SOW are the contract types this product is built around.
    rows = [{
        "id": 22, "role": "assistant",
        "content": "The governing document is the MSA. The cap is Green.",
    }]
    assert validate_segment(
        '[#22 assistant, said earlier] "The cap is Green"', rows, 22, 22
    ) == ""

    rows = [{
        "id": 23, "role": "assistant",
        "content": "I checked it. The cap is Green under our standard position.",
    }]
    assert validate_segment(
        '[#23 assistant, said earlier] "The cap is Green under our standard position"',
        rows, 23, 23,
    ) == ""


def test_sentence_final_company_suffixes_stay_quotable():
    # The cost the narrow list avoids. A full abbreviation list would break all three of
    # these, which is why company suffixes are excluded from it.
    for row_id, content in (
        (25, "We are dealing with Acme Inc. The cap is Green."),
        (26, "Payment goes to Acme Corp. The cap is Green."),
        (27, "Deliver to the office on Main St. The cap is Green."),
    ):
        rows = [{"id": row_id, "role": "assistant", "content": content}]
        assert validate_segment(
            f'[#{row_id} assistant, said earlier] "The cap is Green"',
            rows, row_id, row_id,
        ) == "", f"row {row_id} should stay quotable"


def test_a_question_cannot_be_quoted_as_a_decision():
    # The attorney ASKED whether to accept; dropping the "?" makes the summary say they
    # DECIDED to. A modality flip inside one sentence, built from genuine characters —
    # exactly what this gate exists to make unconstructible.
    rows = [{"id": 30, "role": "user", "content": "We will accept 12 months?"}]
    err = validate_segment('[#30 attorney] "We will accept 12 months"', rows, 30, 30)
    assert "not a complete sentence" in err


def test_a_question_quoted_with_its_mark_is_accepted():
    # The other half: a question is quotable AS a question.
    rows = [{
        "id": 31, "role": "user",
        "content": "Can we drop the exclusivity clause? The client is worried.",
    }]
    assert validate_segment(
        '[#31 attorney] "Can we drop the exclusivity clause?"', rows, 31, 31
    ) == ""


def test_an_exclamation_cannot_be_dropped_either():
    rows = [{"id": 32, "role": "user", "content": "Should we accept! We must decide."}]
    err = validate_segment('[#32 attorney] "Should we accept"', rows, 32, 32)
    assert "not a complete sentence" in err


def test_a_casefold_expanding_character_does_not_desynchronise_indices():
    # casefold() maps ß->ss and the ﬁ ligature->fi, so matching on a folded string while
    # boundaries index the unfolded one shifts every later position. The symptom is a
    # valid sentence being rejected forever, which wedges compaction for the document.
    for row_id, content in (
        (33, "Groß AG asked. We will not accept 12 months."),
        (34, "Beneﬁt terms. We will not accept 12 months."),
    ):
        rows = [{"id": row_id, "role": "user", "content": content}]
        assert validate_segment(
            f'[#{row_id} attorney] "We will not accept 12 months."', rows, row_id, row_id,
        ) == "", f"row {row_id} desynchronised"


def test_a_digit_initial_sentence_is_a_real_boundary():
    # Endemic in contract chat. Decimals stay excluded — "12.5" has no space after the dot.
    rows = [{
        "id": 35, "role": "user",
        "content": "We should push back. 12 months is not acceptable.",
    }]
    assert validate_segment(
        '[#35 attorney] "12 months is not acceptable."', rows, 35, 35
    ) == ""


def test_a_bracketed_sentence_start_is_a_real_boundary():
    rows = [{
        "id": 36, "role": "assistant",
        "content": "The cap is Green. (Section 12.5 is relevant.) We proceed.",
    }]
    assert validate_segment(
        '[#36 assistant, said earlier] "The cap is Green."', rows, 36, 36
    ) == ""


def test_a_bracket_does_not_defeat_the_abbreviation_guard():
    rows = [{
        "id": 37, "role": "user",
        "content": "Escalate to the partner (Mr. Jones is out) but we will accept 12 months.",
    }]
    err = validate_segment(
        '[#37 attorney] "Jones is out) but we will accept 12 months."', rows, 37, 37
    )
    assert "not a complete sentence" in err


def test_a_cross_reference_abbreviation_before_a_number_is_not_a_boundary():
    # Allowing a digit to open a sentence made every "cl. 4.2" and "Sec. 9" a false full
    # stop, so a quote could stop just before the condition it should have carried. These
    # are ordinary in contract chat — more common than the documented Ltd. residual.
    for row_id, content, quote in (
        (40, "We will accept 12 months from 1 Jan. 2026 only if the cap is raised.",
             "We will accept 12 months from 1 Jan."),
        (41, "We will accept the terms in cl. 4.2 only if the cap is raised.",
             "We will accept the terms in cl."),
        (42, "We will accept the wording in Sec. 9 only if signed by Friday.",
             "We will accept the wording in Sec."),
        (43, "We will accept the cap in Ex. 3 only if countersigned.",
             "We will accept the cap in Ex."),
    ):
        rows = [{"id": row_id, "role": "user", "content": content}]
        err = validate_segment(f'[#{row_id} attorney] "{quote}"', rows, row_id, row_id)
        assert "not a complete sentence" in err, f"row {row_id} leaked"


def test_a_digit_initial_sentence_after_an_ordinary_word_still_works():
    # The other half of the trade: the whole point of allowing a digit to open a sentence
    # is that "12 months is not acceptable." is ordinary. "back" is not a cross-reference
    # abbreviation, so it stays a real boundary.
    rows = [{
        "id": 44, "role": "user",
        "content": "We should push back. 12 months is not acceptable.",
    }]
    assert validate_segment(
        '[#44 attorney] "12 months is not acceptable."', rows, 44, 44
    ) == ""


def test_markdown_emphasis_does_not_defeat_the_abbreviation_guard():
    # _token_before's strip set must match _EDGE_CHARS: a character that can sit between
    # two sentences can sit in front of an abbreviation too.
    rows = [{
        "id": 45, "role": "user",
        "content": "Escalate to the partner **Mr. Jones is out** but we will accept 12 months.",
    }]
    err = validate_segment(
        '[#45 attorney] "Jones is out** but we will accept 12 months."', rows, 45, 45
    )
    assert "not a complete sentence" in err


# --- Gaps found in a Word sideload, on a real conversation ----------------------
# Compaction failed on its first real attempt, and none of these was model
# misbehaviour: the model quoted faithfully and our normalisation was too narrow.


def test_a_bullet_after_a_heading_is_quotable():
    """Markdown lists are how the assistant actually answers.

    Collapsing newlines into spaces welded a whole reply into one enormous
    "sentence", so the first bullet under any "**Heading:**" had no boundary in
    front of it and was unquotable. Measured on a real conversation, that single
    shape rejected the segment and compaction never once succeeded.
    """
    rows = [{
        "id": 50, "role": "assistant",
        "content": (
            "I reviewed the SOW.\n\n**Required Actions:**\n"
            "- Fill all placeholders before signature.\n"
            "- Resolve the delay conflict between the MSA and SOW.\n"
        ),
    }]
    assert validate_segment(
        '[#50 assistant, said earlier] "Fill all placeholders before signature."',
        rows, 50, 50,
    ) == ""


def test_a_line_break_cannot_end_a_quote():
    """The other half of the same rule, and why it is asymmetric.

    A line break may BEGIN a sentence — a list item is a genuine start. It may
    never END one, or a hard-wrapped line would let the condition be dropped,
    which is the exact truncation this gate exists to prevent.
    """
    rows = [{
        "id": 51, "role": "user",
        "content": "We will accept 12 months\nonly if the cap is raised.",
    }]
    err = validate_segment('[#51 attorney] "We will accept 12 months"', rows, 51, 51)
    assert "not a complete sentence" in err


def test_a_quote_character_substitution_still_matches():
    """The model MUST substitute here, and it is right to.

    The quote line is itself delimited by double quotes, so text containing one
    cannot be reproduced literally. It wrote 'Consultants' where the row had
    "Consultants" — faithful quoting that our normalisation rejected as absent
    from its own row.
    """
    rows = [{
        "id": 52, "role": "assistant",
        "content": 'The SOW\'s "Consultants" section aligns with the MSA.',
    }]
    assert validate_segment(
        '[#52 assistant, said earlier] "The SOW\'s \'Consultants\' section aligns with the MSA."',
        rows, 52, 52,
    ) == ""


def test_dropped_markdown_formatting_still_matches():
    """The model copies the words, not the code fence around them."""
    rows = [{
        "id": 53, "role": "assistant",
        "content": "Placeholders for `[__]` remain blank.",
    }]
    assert validate_segment(
        '[#53 assistant, said earlier] "Placeholders for [__] remain blank."',
        rows, 53, 53,
    ) == ""


# --- Partitioning: keep what verifies, drop what does not ----------------------


def test_partition_keeps_the_good_quotes_and_drops_the_bad_one():
    """One unverifiable line must not destroy nineteen good ones.

    Measured on a real conversation with the best available model: 20 of 22 quotes
    were perfect and 2 were trimmed mid-sentence. Rejecting the whole segment for
    that would mean compaction essentially never runs — which is not safer than
    dropping two lines, only quieter.
    """
    body = (
        '[#412 attorney] "Please use Suzy Quatro for all signature blocks"\n'
        '[#414 attorney] "we will accept 24 months"\n'
        '[#413 assistant, said earlier] "Done — the cap is Green under the playbook"'
    )
    kept, dropped, fatal = partition_quotes(body, ROWS, 412, 414)
    assert fatal == ""
    assert [q["row_id"] for q in kept] == [412, 413]
    assert len(dropped) == 1
    assert "#414" in dropped[0]


def test_partition_is_fatal_when_nothing_verifies():
    """A segment of nothing is not a segment. Fail loudly, write nothing."""
    body = (
        '[#412 attorney] "something nobody said"\n'
        '[#414 attorney] "we will accept 24 months"'
    )
    kept, dropped, fatal = partition_quotes(body, ROWS, 412, 414)
    assert kept == []
    assert len(dropped) == 2
    assert "no quote could be verified" in fatal


def test_partition_is_fatal_on_unparseable_output():
    kept, dropped, fatal = partition_quotes("here is a summary of it all", ROWS, 412, 414)
    assert kept == []
    assert "not a quote line" in fatal


def test_partition_drops_exactly_what_the_strict_check_rejects():
    """The two paths must share one definition of a trustworthy quote.

    If they could disagree, validate_segment would document a guarantee that the
    path which actually writes segments does not apply.
    """
    cases = [
        '[#999 attorney] "outside the range"',
        '[#413 attorney] "Done — the cap is Green under the playbook"',   # wrong speaker
        '[#414 attorney] "we will accept 24 months"',                     # not present
        '[#412 attorney] "use Suzy Quatro for all signature blocks"',     # mid-sentence
    ]
    for body in cases:
        strict = validate_segment(body, ROWS, 412, 414)
        kept, dropped, fatal = partition_quotes(body, ROWS, 412, 414)
        assert strict != "", f"strict check unexpectedly accepted: {body}"
        assert kept == [], f"partition kept what strict rejected: {body}"
        assert dropped == [strict], f"reasons diverged for: {body}"
