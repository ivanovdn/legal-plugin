"""_load_prior_conversation — durable history load, guards, degraded posture."""
import importlib
from types import SimpleNamespace

ctx = importlib.import_module("skills.legal_research.context")
from memory.conversation_store import append_turn


def _settings(enabled=True, max_messages=20):
    return SimpleNamespace(
        conversation_store_enabled=enabled,
        conversation_max_messages=max_messages,
        # These tests exercise the raw conversation load, not compaction;
        # keep it off so they don't reach into conversation_summary at all.
        compaction_enabled=False,
    )


def test_loads_durable_history(monkeypatch):
    append_turn("doc-1", "atty-1", "q1", "a1")
    monkeypatch.setattr(ctx, "get_settings", lambda: _settings())
    msgs = ctx._load_prior_conversation({"document_id": "doc-1", "user_id": "atty-1"})
    assert [m["content"] for m in msgs] == ["q1", "a1"]


def test_empty_when_disabled(monkeypatch):
    append_turn("doc-1", "atty-1", "q1", "a1")
    monkeypatch.setattr(ctx, "get_settings", lambda: _settings(enabled=False))
    assert ctx._load_prior_conversation({"document_id": "doc-1", "user_id": "atty-1"}) == []


def test_empty_when_ids_missing(monkeypatch):
    monkeypatch.setattr(ctx, "get_settings", lambda: _settings())
    assert ctx._load_prior_conversation({"document_id": "", "user_id": "atty-1"}) == []
    assert ctx._load_prior_conversation({"document_id": "doc-1", "user_id": ""}) == []


def test_read_failure_flags_degraded(monkeypatch):
    monkeypatch.setattr(ctx, "get_settings", lambda: _settings())
    def _boom(*a, **k):
        raise RuntimeError("db gone")
    monkeypatch.setattr(ctx, "load_recent", _boom)
    state = {"document_id": "doc-1", "user_id": "atty-1"}
    assert ctx._load_prior_conversation(state) == []
    assert state["memory_degraded"] is True


def test_injection_puts_summaries_before_verbatim_rows(monkeypatch):
    from memory.conversation_store import append_turn, load_rows_after
    from memory.conversation_summary import append_segment
    import skills.legal_research.context as ctx

    for i in range(5):
        append_turn("doc-c", "atty-c", f"q{i}", f"a{i}")
    rows = load_rows_after("doc-c", "atty-c", 0, 100)
    append_segment("doc-c", "atty-c", rows[0]["id"], rows[3]["id"], "SEGMENT ONE")

    state = {"document_id": "doc-c", "user_id": "atty-c"}
    out = ctx._load_prior_conversation(state)

    assert out[0] == {"role": "system", "content": "SEGMENT ONE"}
    # Rows 1-4 are condensed; replaying them verbatim would undo the compaction.
    assert [m["content"] for m in out[1:]] == ["q2", "a2", "q3", "a3", "q4", "a4"]


def test_injection_is_oldest_first_and_windowed(monkeypatch):
    from memory.conversation_store import append_turn, load_rows_after
    from memory.conversation_summary import append_segment
    import skills.legal_research.context as ctx

    for i in range(10):
        append_turn("doc-d", "atty-d", f"q{i}", f"a{i}")
    rows = load_rows_after("doc-d", "atty-d", 0, 100)
    for n in range(4):
        append_segment("doc-d", "atty-d", rows[n * 2]["id"], rows[n * 2 + 1]["id"], f"SEG{n}")

    monkeypatch.setattr(
        ctx, "get_settings",
        lambda: type("S", (), {
            "conversation_store_enabled": True, "conversation_max_messages": 20,
            "compaction_enabled": True, "compaction_max_injected_segments": 3,
        })(),
    )
    out = ctx._load_prior_conversation({"document_id": "doc-d", "user_id": "atty-d"})
    injected = [m["content"] for m in out if m["role"] == "system"]
    # The most recent 3 segments, chronological.
    assert injected == ["SEG1", "SEG2", "SEG3"]
    # The verbatim floor spans ALL segments, not just the injected window —
    # otherwise SEG0's rows would come back verbatim and the compaction that
    # condensed them would be undone.
    assert [m["content"] for m in out if m["role"] != "system"][0] == "q4"


def test_a_summary_read_failure_degrades_to_raw_rows(monkeypatch):
    from memory.conversation_store import append_turn
    import skills.legal_research.context as ctx

    append_turn("doc-e", "atty-e", "q", "a")

    def boom(*_a, **_k):
        raise RuntimeError("app-db unavailable")

    monkeypatch.setattr(ctx, "latest_to_id", boom)
    state = {"document_id": "doc-e", "user_id": "atty-e"}
    out = ctx._load_prior_conversation(state)
    # Degrades to the raw conversation and says so — a compaction failure must
    # never break a subsequent chat turn.
    assert [m["content"] for m in out] == ["q", "a"]
    assert state["memory_degraded"] is True


def test_a_conversation_read_failure_returns_nothing_and_flags(monkeypatch):
    import skills.legal_research.context as ctx

    def boom(*_a, **_k):
        raise RuntimeError("app-db unavailable")

    monkeypatch.setattr(ctx, "load_recent", boom)
    state = {"document_id": "doc-g", "user_id": "atty-g"}
    # The OTHER failure path: with no rows readable there is nothing to degrade
    # to, so the turn answers from the Redis fallback in _run_doc_chat. Two
    # distinct paths, deliberately: losing the summaries is survivable, losing
    # the conversation is not.
    assert ctx._load_prior_conversation(state) == []
    assert state["memory_degraded"] is True


def test_compaction_disabled_ignores_existing_segments(monkeypatch):
    from memory.conversation_store import append_turn, load_rows_after
    from memory.conversation_summary import append_segment
    import skills.legal_research.context as ctx

    append_turn("doc-f", "atty-f", "q0", "a0")
    append_turn("doc-f", "atty-f", "q1", "a1")
    rows = load_rows_after("doc-f", "atty-f", 0, 100)
    append_segment("doc-f", "atty-f", rows[0]["id"], rows[1]["id"], "SEG")

    monkeypatch.setattr(
        ctx, "get_settings",
        lambda: type("S", (), {
            "conversation_store_enabled": True, "conversation_max_messages": 20,
            "compaction_enabled": False, "compaction_max_injected_segments": 3,
        })(),
    )
    out = ctx._load_prior_conversation({"document_id": "doc-f", "user_id": "atty-f"})
    # The master switch is a real off: no segment injected AND no floor applied.
    assert [m["content"] for m in out] == ["q0", "a0", "q1", "a1"]
