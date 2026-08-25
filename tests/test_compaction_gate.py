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
    assert "not a complete sentence or clause" in err


def test_a_prefix_that_drops_a_trailing_condition_is_rejected():
    # The mirror image: a genuine prefix that silently drops the condition the
    # acceptance depended on.
    rows = [{
        "id": 8, "role": "user",
        "content": "We will accept 12 months only if the cap is raised.",
    }]
    err = validate_segment('[#8 attorney] "We will accept 12 months"', rows, 8, 8)
    assert "not a complete sentence or clause" in err


def test_a_complete_clause_from_the_middle_of_a_message_is_accepted():
    # Clause-aligned at both ends, so the trim cannot have inverted the meaning.
    # This is what the gate must keep ALLOWING — a rule that only rejects is a rule
    # that kills the feature.
    rows = [{
        "id": 9, "role": "assistant",
        "content": "I checked the playbook. The cap is Green; escalation is not required.",
    }]
    assert validate_segment(
        '[#9 assistant, said earlier] "The cap is Green"', rows, 9, 9
    ) == ""
