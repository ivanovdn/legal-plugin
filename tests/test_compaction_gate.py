"""The compaction validation gate — a deterministic, zero-LLM guard on an LLM's output.

Every quote carries the conversation_store row id it came from, so a fabricated
line is not merely detectable, it is REJECTABLE. Any failing quote invalidates
the whole segment: a summary is legal recall, and one invented line in it is
worse than no summary at all.
"""
from skills.legal_research.compaction import (
    parse_quote_lines,
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


def test_residual_company_suffix_before_a_capitalised_name_is_not_caught():
    # Still open, deliberately. "Ltd." is not in _NON_TERMINAL_ABBREVIATIONS because
    # company suffixes commonly END a sentence in this domain, and listing them cost
    # three legitimate quotes (see the module docstring) to close this one shape. Pinned
    # so it stays visible: if someone adds "Ltd" to the list, this test fails and forces
    # them to weigh that trade consciously.
    rows = [{
        "id": 24, "role": "user",
        "content": "We will accept payment from Acme Ltd. Partners only if wired by Monday.",
    }]
    assert validate_segment(
        '[#24 attorney] "We will accept payment from Acme Ltd"', rows, 24, 24
    ) == ""


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
