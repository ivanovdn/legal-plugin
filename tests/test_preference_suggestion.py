from graph.nodes.output_formatter import output_formatter
from skills.legal_research import _extract_proposed_preferences


def test_extract_single_line():
    prose = "Sure, I'll remember that.\n```preference\nAlways flag uncapped indemnity as Red.\n```"
    assert _extract_proposed_preferences(prose) == ["Always flag uncapped indemnity as Red."]


def test_extract_multiple_lines_and_strips_bullets():
    prose = "```preference\n- Delaware governing law fallback.\n- Surface auto-renewal.\n```"
    assert _extract_proposed_preferences(prose) == [
        "Delaware governing law fallback.",
        "Surface auto-renewal.",
    ]


def test_extract_none_when_no_block():
    assert _extract_proposed_preferences("Just a normal answer, no block.") == []


def test_extract_ignores_json_edit_block():
    prose = '```json\n{"action":"replace","target_text":"a","new_text":"b"}\n```'
    assert _extract_proposed_preferences(prose) == []


def test_output_formatter_surfaces_preferences():
    state = {"task_type": "research", "proposed_preferences": ["p1"]}
    out = output_formatter(state)
    assert out["report"]["proposed_preferences"] == ["p1"]
