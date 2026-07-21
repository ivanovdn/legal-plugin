import pytest

from memory.preferences import (
    PreferenceTooLargeError,
    _MAX_WRITE_CHARS,
    load_preferences,
    save_preferences,
)


def test_save_then_load_round_trip(tmp_path):
    save_preferences(str(tmp_path), "atty-1", "# Prefs\n- Always flag uncapped indemnity.")
    assert load_preferences(str(tmp_path), "atty-1") == "# Prefs\n- Always flag uncapped indemnity."


def test_load_missing_returns_empty(tmp_path):
    assert load_preferences(str(tmp_path), "nobody") == ""


def test_save_creates_nested_dir(tmp_path):
    base = tmp_path / "does" / "not" / "exist"
    save_preferences(str(base), "atty-2", "hi")
    assert (base / "atty-2" / "USER.md").read_text(encoding="utf-8") == "hi"


def test_two_attorneys_isolated(tmp_path):
    save_preferences(str(tmp_path), "a", "alpha")
    save_preferences(str(tmp_path), "b", "beta")
    assert load_preferences(str(tmp_path), "a") == "alpha"
    assert load_preferences(str(tmp_path), "b") == "beta"


@pytest.mark.parametrize("bad", ["../escape", "a/b", "..", "", "with space", "semi;colon"])
def test_unsafe_attorney_id_rejected(tmp_path, bad):
    with pytest.raises(ValueError):
        load_preferences(str(tmp_path), bad)
    with pytest.raises(ValueError):
        save_preferences(str(tmp_path), bad, "x")


def test_oversize_write_rejected(tmp_path):
    with pytest.raises(PreferenceTooLargeError):
        save_preferences(str(tmp_path), "atty-3", "x" * (_MAX_WRITE_CHARS + 1))
