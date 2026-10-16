"""turn_log — the pure row builder and the quiet writer (spec 2026-10-09)."""
from __future__ import annotations

import hashlib
import logging

import pytest
from psycopg.errors import UniqueViolation

import memory.turn_log as turn_log
from memory.db import get_pool, init_db
from memory.turn_log import COLUMNS, build_turn_row, write_turn, write_turn_quietly

_CLIENT = {"office_platform": "Mac", "office_version": "16.89",
           "pane_build": "abc1234", "user_agent": "Mozilla/5.0 (Macintosh)"}


def _row(**overrides):
    args = dict(
        kind="query", turn_id="t-1", trace_id="f" * 32,
        arrived_at="2026-10-09T10:00:00+00:00", duration_ms=13100,
        attorney_id="atty-1", user_name="A. Tester", session_id="s-1",
        document_id="doc-req", task_type="research", request="Is the cap market?",
        doc_text="STATEMENT OF WORK", status="ok", outcome="ok", degradations=[],
        api_error="", awaiting_review=False, facts={}, result=None, compaction=None,
        client=_CLIENT, backend_build="abc1234",
        runtime={"settings": {"ollama_num_ctx": 131072, "ollama_think_review": True},
                 "model_digest": "aaa111", "ollama_version": "0.12.3"},
    )
    args.update(overrides)
    return build_turn_row(**args)


def test_the_row_has_exactly_the_tables_columns():
    assert set(_row()) == set(COLUMNS)
    assert len(COLUMNS) == len(set(COLUMNS))


# turn_log's columns as first deployed (VM, 2026-10-14). Frozen: never edit this
# list — it stands for every database created before the next column change.
_SHIPPED_2026_10_14 = (
    "timestamp", "turn_id", "trace_id", "kind", "session_id", "document_id",
    "attorney_id", "user_name", "task_type", "request", "doc_chars", "doc_sha256",
    "status", "outcome", "degradations", "error", "awaiting_review", "contract_type",
    "contract_type_defaulted", "msa_title", "preferences_chars", "doc_kept_pct",
    "context", "duration_ms", "llm_calls", "llm_load_ms", "llm_prompt_ms",
    "llm_gen_ms", "tokens_in", "tokens_out", "llm_detail", "response_chars",
    "edits_proposed", "preferences_proposed", "compaction", "office_platform",
    "office_version", "pane_build", "backend_build", "user_agent",
)


def _turn_log_columns(conn) -> set[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'turn_log' AND column_name <> 'id'"
    ).fetchall()
    return {r[0] for r in rows}


def test_a_database_from_the_first_deploy_gets_every_column_the_code_writes():
    """The VM's turn_log already exists, so CREATE TABLE IF NOT EXISTS never runs
    there again: a column added only inside the CREATE passes every test on a
    fresh database and fails every insert in production — quietly, into the app
    log. Rebuild the table as it shipped, start up as the VM does, and require
    exactly COLUMNS. A new column needs an ALTER ... ADD COLUMN IF NOT EXISTS
    (memory/db.py); a removed one needs its DROP.

    Mutation: add a column to COLUMNS and to the CREATE only."""
    with get_pool().connection() as conn:
        conn.execute("DROP TABLE turn_log")
        conn.execute(
            "CREATE TABLE turn_log (id BIGSERIAL PRIMARY KEY, "
            + ", ".join(f"{c} TEXT" for c in _SHIPPED_2026_10_14) + ")"
        )
    try:
        init_db()
        with get_pool().connection() as conn:
            assert _turn_log_columns(conn) == set(COLUMNS)
    finally:
        # Every other test needs the real column types back.
        with get_pool().connection() as conn:
            conn.execute("DROP TABLE turn_log")
        init_db()


def test_a_chat_turn_takes_what_ran_from_the_final_state():
    breakdown = {"total_chars": 52000, "parts": [{"key": "document", "chars": 9000}]}
    row = _row(
        facts={"contract_type": "sow", "contract_type_defaulted": False,
               "msa_title": "Trinetix Model MSA", "preferences_chars": 340},
        result={"document_id": "doc-resolved", "task_type": "research",
                "llm_response": "x" * 812, "proposed_edits": [{}, {}],
                "proposed_preferences": [], "context_breakdown": breakdown,
                "context_truncated": None},
    )
    assert row["document_id"] == "doc-resolved"
    assert (row["contract_type"], row["msa_title"], row["preferences_chars"]) == (
        "sow", "Trinetix Model MSA", 340)
    assert row["contract_type_defaulted"] is False
    assert (row["response_chars"], row["edits_proposed"], row["preferences_proposed"]) == (812, 2, 0)
    assert row["context"] == breakdown and row["doc_kept_pct"] is None
    assert (row["office_platform"], row["pane_build"], row["backend_build"]) == (
        "Mac", "abc1234", "abc1234")


def test_a_truncated_turn_records_how_much_of_the_document_was_kept():
    row = _row(outcome="degraded", degradations=["context_truncated"],
               result={"context_truncated": {"doc_chars": 200000, "kept_chars": 116000,
                                             "kept_pct": 58}})
    assert row["doc_kept_pct"] == 58
    assert row["degradations"] == ["context_truncated"]


def test_a_crashed_turn_falls_back_to_the_request_and_names_the_failure():
    row = _row(status="error", outcome="failed", degradations=["graph_invoke_failed"],
               api_error="node exploded",
               facts={"error": "graph_invoke_failed: ValueError: node exploded"})
    assert (row["document_id"], row["task_type"]) == ("doc-req", "research")
    assert row["error"] == "graph_invoke_failed: ValueError: node exploded"
    assert row["response_chars"] == 0 and row["context"] is None


def test_without_a_recorded_failure_the_api_error_text_is_kept():
    row = _row(kind="resume", status="error", api_error="session expired or not found")
    assert row["error"] == "session expired or not found"


def test_a_paused_review_is_marked_awaiting_review():
    row = _row(task_type="contract_review", awaiting_review=True,
               result={"task_type": "contract_review", "llm_response": "draft"})
    assert row["awaiting_review"] is True and row["response_chars"] == 5


def test_a_compaction_row_keeps_the_result_and_no_document():
    result = {"compacted": False, "reason": "nothing earlier to condense yet", "error": ""}
    row = _row(kind="compact", session_id="", task_type="", request="", doc_text="",
               compaction=result)
    assert row["kind"] == "compact" and row["compaction"] == result
    assert (row["doc_chars"], row["doc_sha256"]) == (0, "")


def test_the_row_records_the_settings_and_the_model_it_ran_with():
    row = _row()
    assert row["settings"] == {"ollama_num_ctx": 131072, "ollama_think_review": True}
    assert row["model_digest"] == "aaa111"
    assert row["ollama_version"] == "0.12.3"


def test_a_turn_with_no_runtime_facts_records_empty_ones():
    row = _row(runtime={})
    assert (row["settings"], row["model_digest"], row["ollama_version"]) == ({}, "", "")


def test_the_fingerprint_is_the_size_and_sha256_of_the_text():
    row = _row(doc_text="abc")
    assert row["doc_chars"] == 3
    assert row["doc_sha256"] == hashlib.sha256(b"abc").hexdigest()


def test_long_questions_and_errors_are_capped():
    row = _row(request="q" * 5000, api_error="e" * 5000, status="error")
    assert len(row["request"]) == 2000 and len(row["error"]) == 2000


def test_model_time_and_tokens_are_summed_over_every_call():
    calls = [
        {"name": "doc_chat", "model": "m", "tokens_in": 9000, "tokens_out": 120,
         "load_ms": 5900, "prompt_ms": 6100, "gen_ms": 1100, "total_ms": 13200},
        {"name": "doc_chat_json_retry", "model": "m", "tokens_in": None, "tokens_out": None,
         "load_ms": None, "prompt_ms": 800, "gen_ms": 300, "total_ms": None},
    ]
    row = _row(facts={"llm_detail": calls})
    assert row["llm_calls"] == 2 and row["llm_detail"] == calls
    assert (row["llm_load_ms"], row["llm_prompt_ms"], row["llm_gen_ms"]) == (5900, 6900, 1400)
    assert (row["tokens_in"], row["tokens_out"]) == (9000, 120)


def test_a_lone_surrogate_breaks_neither_the_fingerprint_nor_the_write():
    """Review Focus #4. Broken UTF-16 from Word or a paste reaches Python as a
    lone surrogate; UTF-8 cannot encode one, so an unguarded hash or INSERT
    would lose the row."""
    row = _row(turn_id="t-sur", doc_text="pay \ud83d now", request="is \ud83d fine?")
    assert row["doc_chars"] == 9 and len(row["doc_sha256"]) == 64
    assert row["request"] == "is ? fine?"
    write_turn(row)                                     # must not raise


def test_an_msa_whose_title_is_null_still_writes_its_row():
    """get_parent_msa passes a Qdrant payload's doc_title straight through, and a
    payload holding null gives None (the default only covers an absent key) —
    which NOT NULL would then refuse, losing the row to the log."""
    row = _row(turn_id="t-no-title", facts={"msa_title": None})
    assert row["msa_title"] == ""
    write_turn(row)                                     # must not raise


def test_a_written_row_reads_back_with_its_json_columns_intact():
    write_turn(_row(degradations=["context_truncated"],
                    facts={"llm_detail": [{"name": "doc_chat", "tokens_in": 5}]},
                    result={"context_breakdown": {"pct": 61}}))
    with get_pool().connection() as conn:
        got = conn.execute(
            "SELECT degradations, llm_detail, context, compaction, doc_kept_pct "
            "FROM turn_log WHERE turn_id = 't-1'"
        ).fetchone()
    assert got[0] == ["context_truncated"]
    assert got[1][0]["name"] == "doc_chat"
    assert got[2] == {"pct": 61} and got[3] is None and got[4] is None


def test_the_settings_read_back_as_json():
    write_turn(_row())
    with get_pool().connection() as conn:
        got = conn.execute(
            "SELECT settings, model_digest, ollama_version FROM turn_log WHERE turn_id = 't-1'"
        ).fetchone()
    assert got == ({"ollama_num_ctx": 131072, "ollama_think_review": True}, "aaa111", "0.12.3")


def test_turn_id_is_unique():
    write_turn(_row())
    with pytest.raises(UniqueViolation):
        write_turn(_row())


def test_a_failed_write_logs_the_whole_row_and_never_raises(monkeypatch, caplog):
    def boom():
        raise RuntimeError("pool timeout")

    monkeypatch.setattr(turn_log, "get_pool", boom)
    with caplog.at_level(logging.ERROR, logger="memory.turn_log"):
        write_turn_quietly(_row(turn_id="t-lost"))      # must not raise
    assert "TURN LOG WRITE FAILED" in caplog.text and "t-lost" in caplog.text


def test_a_nul_in_the_question_does_not_lose_the_row():
    """Postgres TEXT refuses NUL; memory/fingerprint.py's storable() drops it.
    Mutation: storable() without the NUL replacement."""
    row = _row(turn_id="t-nul", request="is \x00 fine?")
    assert row["request"] == "is  fine?"
    write_turn(row)                                     # must not raise
