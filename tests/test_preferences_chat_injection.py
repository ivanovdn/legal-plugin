from config import get_settings
from memory.preferences import save_preferences
from skills import legal_research


def _capture(monkeypatch):
    """Monkeypatch the LLM call to capture the assembled messages."""
    seen = {}

    class _Resp:
        content = "Two directors sign per Section 9."

    def fake_invoke(llm, messages, name=None):
        seen["messages"] = messages
        return _Resp()

    monkeypatch.setattr(legal_research, "traced_invoke", fake_invoke)
    return seen


def test_preferences_injected_after_system_prompt(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "preferences_dir", str(tmp_path))
    monkeypatch.setattr(s, "preferences_enabled", True)
    save_preferences(str(tmp_path), "atty-77", "- Always flag uncapped indemnity.")
    seen = _capture(monkeypatch)

    state = {
        "request": "Who signs this?",           # plain question → grounding gate off
        "user_id": "atty-77",
        "filters": {},
        "chat_history": [],
    }
    legal_research._run_doc_chat(state, "MUTUAL NON-DISCLOSURE AGREEMENT\n\n1. Term ...")

    systems = [m for m in seen["messages"] if m["role"] == "system"]
    assert "ATTORNEY PREFERENCES" in systems[1]["content"]          # right after CHAT_SYSTEM_PROMPT
    assert "Always flag uncapped indemnity" in systems[1]["content"]
    # never the last message (the user doc message is last)
    assert seen["messages"][-1]["role"] == "user"


def test_no_preferences_when_disabled(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "preferences_dir", str(tmp_path))
    monkeypatch.setattr(s, "preferences_enabled", False)
    save_preferences(str(tmp_path), "atty-77", "- pref")
    seen = _capture(monkeypatch)

    state = {"request": "Who signs?", "user_id": "atty-77", "filters": {}, "chat_history": []}
    legal_research._run_doc_chat(state, "MUTUAL NON-DISCLOSURE AGREEMENT")
    assert all("ATTORNEY PREFERENCES" not in m["content"] for m in seen["messages"])
