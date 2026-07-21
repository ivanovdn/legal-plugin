from config import get_settings
from skills.grounding import (
    _PREFERENCES_DIRECTIVE,
    load_attorney_preferences_block,
    preferences_block_for_state,
)
from memory.preferences import save_preferences


def test_block_empty_when_no_file(tmp_path):
    assert load_attorney_preferences_block("atty-x", str(tmp_path), 8000) == ""


def test_block_wraps_directive(tmp_path):
    save_preferences(str(tmp_path), "atty-x", "- Always flag uncapped indemnity.")
    block = load_attorney_preferences_block("atty-x", str(tmp_path), 8000)
    assert _PREFERENCES_DIRECTIVE.strip()[:20] in block
    assert "Always flag uncapped indemnity" in block
    assert "END ATTORNEY PREFERENCES" in block


def test_block_truncates(tmp_path):
    save_preferences(str(tmp_path), "atty-x", "y" * 500)
    block = load_attorney_preferences_block("atty-x", str(tmp_path), 100)
    assert "truncated to 100" in block
    assert block.count("y") >= 100


def test_block_empty_on_load_error(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise OSError("disk gone")
    monkeypatch.setattr("skills.grounding.load_preferences", boom)
    assert load_attorney_preferences_block("atty-x", str(tmp_path), 8000) == ""


def test_block_empty_for_whitespace_only(tmp_path):
    save_preferences(str(tmp_path), "atty-x", "   \n\n  ")
    assert load_attorney_preferences_block("atty-x", str(tmp_path), 8000) == ""


def test_state_wrapper_gated_and_keyed(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "preferences_dir", str(tmp_path))
    monkeypatch.setattr(s, "preferences_enabled", True)
    save_preferences(str(tmp_path), "atty-9", "- pref line")
    assert "pref line" in preferences_block_for_state({"user_id": "atty-9"})
    # disabled -> empty
    monkeypatch.setattr(s, "preferences_enabled", False)
    assert preferences_block_for_state({"user_id": "atty-9"}) == ""
    # no user_id -> empty
    monkeypatch.setattr(s, "preferences_enabled", True)
    assert preferences_block_for_state({}) == ""
