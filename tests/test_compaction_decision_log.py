"""Every doc-chat turn must record why auto-compaction did or did not fire.

Nothing else logs the decision: compaction.py speaks only once a run is already
underway, so a turn where auto stayed silent left no trace at all. On the VM
(2026-08-27) that turned "auto-compaction is not firing" into three round trips
of screenshots and after-the-fact SQL, and it still did not separate the two
candidate faults — the backend withholding the flag, or the pane ignoring it.

The line has to be present on the QUIET turns, which is the case a naive test
skips: an armed-and-fired turn is already visible through the run's own logs.
"""

import importlib
import logging

from config import get_settings

legal_research = importlib.import_module("skills.legal_research.legal_research")


def _state(**over):
    base = {
        "request": "who signs?", "user_id": "a-1", "uploaded_docs": [], "task_type": "",
        "skill_plan": [], "retrieval_query": "", "retrieved_chunks": [], "filters": {},
        "messages": [], "llm_response": "", "risk_level": "", "risk_flags": [],
        "awaiting_review": False, "attorney_notes": "", "report": {},
        "session_id": "s", "document_id": "doc-42", "checkpoint_ref": "",
        "trace_id": "", "chat_history": [], "review_iterations": 0,
        "report_notes_unincorporated": "",
    }
    base.update(over)
    return base


class _Resp:
    content = "answer"


def _run(monkeypatch, caplog, *, compressible, budget, doc):
    """Drive one doc-chat turn and hand back (state, log text)."""
    monkeypatch.setenv("CHAT_CONTEXT_MAX_CHARS", str(budget))
    get_settings.cache_clear()
    # compressible_history is re-imported into the ENTRY module, and _run_doc_chat
    # resolves it in THAT module's globals — patching context.py here would
    # silently no-op and green a test that tested nothing.
    monkeypatch.setattr(legal_research, "compressible_history", lambda _s: compressible)
    monkeypatch.setattr(legal_research, "_build_llm", lambda: object())
    monkeypatch.setattr(legal_research, "traced_invoke", lambda *a, **k: _Resp())

    state = _state()
    with caplog.at_level(logging.INFO):
        legal_research._run_doc_chat(state, doc)
    return state, caplog.text


def _expected(state, compressible):
    """The line the diagnostic is supposed to print, rebuilt from the breakdown.

    Built from what the pane was ACTUALLY sent, so the assertion catches a line
    that disagrees with the verdict it claims to report. The caller still has to
    assert the verdict itself — consistency alone would pass on a wrong answer.
    """
    s, bd = get_settings(), state["context_breakdown"]
    msgs, chars = compressible
    truncated = bool(state["context_truncated"])
    return (
        f"[compaction] auto={bd['auto_compact']} can={bd['can_compact']} "
        f"pct={bd['pct']}/{bd['warn_pct']} compressible={msgs} msgs/{chars} chars "
        f"floor={s.compaction_auto_min_chars} chars truncated={truncated} "
        f"enabled={s.compaction_enabled} auto_cfg={s.compaction_auto} doc=doc-42"
    )


def test_the_line_names_the_floor_that_held_when_auto_stays_silent(monkeypatch, caplog):
    """Over the warn line, but too little to be worth a call.

    Two messages of 100 chars is under the floor, so auto is correctly quiet — and
    the line has to make that legible without a database session.
    """
    compressible = (2, 100)
    state, text = _run(monkeypatch, caplog, compressible=compressible,
                       budget=1000, doc="NON-DISCLOSURE AGREEMENT\n\n1. Term " + "x" * 2000)
    bd = state["context_breakdown"]

    assert bd["pct"] >= bd["warn_pct"], "meant to be under pressure"
    assert bd["can_compact"] is True and bd["auto_compact"] is False
    assert _expected(state, compressible) in text
    get_settings.cache_clear()


def test_the_line_reports_an_armed_turn_so_a_missing_run_indicts_the_pane(monkeypatch, caplog):
    """auto=True with no `[compaction] condensed` after it means the client dropped it.

    That is the half the backend cannot otherwise evidence, and it is exactly
    what the pane's disarm latch looks like from the server.
    """
    compressible = (6, 90000)
    state, text = _run(monkeypatch, caplog, compressible=compressible,
                       budget=1000, doc="NON-DISCLOSURE AGREEMENT\n\n1. Term " + "x" * 2000)
    bd = state["context_breakdown"]

    assert bd["auto_compact"] is True
    assert _expected(state, compressible) in text
    get_settings.cache_clear()


def test_the_line_is_printed_on_an_unpressured_turn_too(monkeypatch, caplog):
    """Silence must never be ambiguous.

    A line only on interesting turns would leave "nothing logged" meaning both
    "not under pressure" and "the log was never reached" — the ambiguity this
    whole diagnostic exists to remove.
    """
    compressible = (8, 90000)
    state, text = _run(monkeypatch, caplog, compressible=compressible,
                       budget=150000, doc="NON-DISCLOSURE AGREEMENT\n\n1. Term ...")
    bd = state["context_breakdown"]

    assert bd["pct"] < bd["warn_pct"]
    assert bd["can_compact"] is False and bd["auto_compact"] is False
    assert _expected(state, compressible) in text
    get_settings.cache_clear()


def test_the_line_reports_truncation_so_the_worst_case_is_one_grep(monkeypatch, caplog):
    """`truncated=True auto=False` is the pathology of 2026-08-27 in a single line.

    Cutting the contract while condensable history sits there is the exact failure
    this feature exists to prevent, and it used to take two log lines from
    different modules plus a screenshot to see it. Now it is one grep.
    """
    compressible = (2, 100)
    state, text = _run(monkeypatch, caplog, compressible=compressible,
                       budget=1000, doc="NON-DISCLOSURE AGREEMENT\n\n1. Term " + "x" * 2000)

    assert state["context_truncated"], "the budget is small enough to force a cut"
    assert "truncated=True" in text
    assert _expected(state, compressible) in text
    get_settings.cache_clear()
