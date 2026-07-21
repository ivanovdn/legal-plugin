from config import get_settings
from memory.preferences import save_preferences
from skills.contract_review import contract_review
from skills.contract_review.contract_review import _OUTPUT_CONSTRAINTS


def test_preferences_after_playbook_before_output_constraints(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "preferences_dir", str(tmp_path))
    monkeypatch.setattr(s, "preferences_enabled", True)
    save_preferences(str(tmp_path), "atty-5", "- Always flag uncapped indemnity.")

    state = {
        "request": "Review this contract.",
        "user_id": "atty-5",
        "uploaded_docs": [{"text": "MUTUAL NON-DISCLOSURE AGREEMENT\n\n1. Term ..."}],
        "filters": {},
    }
    out = contract_review(state)
    systems = [m["content"] for m in out["messages"] if m["role"] == "system"]
    pref_idx = next(i for i, c in enumerate(systems) if "ATTORNEY PREFERENCES" in c)
    oc_idx = next(i for i, c in enumerate(systems) if c == _OUTPUT_CONSTRAINTS)
    assert 0 < pref_idx < oc_idx           # after playbook (index 0), before output constraints
    assert out["messages"][-1]["role"] == "user"   # prefs never last


def test_no_preferences_when_empty(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "preferences_dir", str(tmp_path))
    monkeypatch.setattr(s, "preferences_enabled", True)
    state = {
        "request": "Review this contract.",
        "user_id": "atty-empty",
        "uploaded_docs": [{"text": "MUTUAL NON-DISCLOSURE AGREEMENT"}],
        "filters": {},
    }
    out = contract_review(state)
    assert all("ATTORNEY PREFERENCES" not in m["content"] for m in out["messages"])
