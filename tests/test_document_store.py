"""SP4's version store: each distinct text of a document once, under the hash
turn_log already records — and nothing of the text in a failure's log line
(spec 2026-10-15, D1, D2, D6)."""
from __future__ import annotations

import hashlib
import logging

import memory.document_store as ds
from memory.db import get_pool
from memory.fingerprint import fingerprint
from memory.turn_log import build_turn_row


def _versions() -> list[dict]:
    with get_pool().connection() as conn:
        cur = conn.execute("SELECT * FROM document_version ORDER BY document_id, first_seen")
        names = [column.name for column in cur.description]
        return [dict(zip(names, values)) for values in cur.fetchall()]


def _turn_row(doc_text: str) -> dict:
    return build_turn_row(
        kind="query", turn_id="t-1", trace_id="", arrived_at="2026-10-16T09:00:00+00:00",
        duration_ms=1, attorney_id="atty-1", user_name="", session_id="s-1",
        document_id="doc-1", task_type="research", request="who signs?", doc_text=doc_text,
        status="ok", outcome="ok", degradations=[], api_error="", awaiting_review=False,
        facts={}, result=None, compaction=None, client={}, backend_build="dev", runtime={},
    )


def test_a_repeated_text_is_stored_once():
    """Mutation: drop ON CONFLICT … DO NOTHING, or key the row on first_seen."""
    ds.save_version("doc-1", "NDA v1", attorney_id="atty-1", source="turn")
    ds.save_version("doc-1", "NDA v1", attorney_id="atty-1", source="pane")
    (version,) = _versions()
    assert (version["source"], version["text"], version["chars"]) == ("turn", "NDA v1", 6)


def test_the_same_text_in_two_documents_is_two_versions():
    ds.save_version("doc-1", "same words", attorney_id="atty-1", source="turn")
    ds.save_version("doc-2", "same words", attorney_id="atty-1", source="turn")
    assert [v["document_id"] for v in _versions()] == ["doc-1", "doc-2"]


def test_a_turn_finds_its_text_under_the_hash_it_recorded():
    """turn_log and the store share memory/fingerprint.py. A lone surrogate is
    where two hash functions would part (surrogatepass vs replace).
    Mutation: give turn_log its own hash again."""
    text = "pay \ud83d now"
    sha = ds.save_version("doc-1", text, attorney_id="atty-1", source="turn")
    assert sha == _turn_row(text)["doc_sha256"] == fingerprint(text)[1]
    assert _versions()[0]["sha256"] == sha


def test_the_hash_is_sha256_of_the_utf8_text():
    assert fingerprint("abc") == (3, hashlib.sha256(b"abc").hexdigest())
    assert fingerprint("") == (0, "")


def test_a_capped_text_keeps_the_full_texts_hash():
    """Mutation: hash after capping."""
    text = "x" * (ds.TEXT_CAP + 1)
    sha = ds.save_version("doc-1", text, attorney_id="atty-1", source="pane")
    (version,) = _versions()
    assert sha == fingerprint(text)[1] == version["sha256"]
    assert len(version["text"]) == ds.TEXT_CAP
    assert version["chars"] == ds.TEXT_CAP + 1 and version["truncated"] is True


def test_an_empty_text_stores_nothing():
    """Review Focus 4: a document that is all tracked insertions has an empty
    original view; that is not a version. Mutation: drop the early return."""
    assert ds.save_version("doc-1", "", attorney_id="atty-1", source="pane") == ""
    assert _versions() == []


def test_a_lone_surrogate_or_a_nul_does_not_lose_the_version():
    """Review Focus 1: Postgres refuses both; the text is stored cleaned, under
    the hash of what was sent. Mutation: store the text without storable()."""
    text = "pay \ud83d now\x00"
    sha = ds.save_version("doc-1", text, attorney_id="atty-1", source="pane")
    (version,) = _versions()
    assert version["text"] == "pay ? now" and sha == fingerprint(text)[1]


def test_a_failed_write_logs_what_identifies_the_text_and_never_the_text(monkeypatch, caplog):
    """Review Focus 2: Postgres quotes offending input back in some messages,
    so the exception's message stays out of the log too.
    Mutation: log `e` instead of type(e).__name__, or log the text."""
    secret = "The Supplier shall indemnify the Client"

    def boom():
        raise RuntimeError(f"invalid input syntax: {secret}")

    monkeypatch.setattr(ds, "get_pool", boom)
    with caplog.at_level(logging.WARNING, logger="memory.document_store"):
        ds.save_version_quietly("doc-1", secret, attorney_id="atty-1", source="turn")
    assert "doc-1" in caplog.text and fingerprint(secret)[1] in caplog.text
    assert "RuntimeError" in caplog.text and "indemnify" not in caplog.text


def _snapshots() -> list[dict]:
    with get_pool().connection() as conn:
        cur = conn.execute("SELECT * FROM document_snapshot ORDER BY id")
        names = [column.name for column in cur.description]
        return [dict(zip(names, values)) for values in cur.fetchall()]


def _snap(**over):
    kw = dict(attorney_id="atty-1", session_id="s-1", document_id="doc-1", moment="idle",
              current_text="NDA with Acme Ltd", original_text="NDA with [__]",
              tracked_changes=[{"type": "Added", "author": "Jane Smith",
                                "date": "2026-10-16T09:00:00.000Z", "text": "Acme Ltd"}],
              tracked_total=1, client={"office_platform": "Mac"}, backend_build="b1")
    kw.update(over)
    ds.save_snapshot(**kw)


def test_a_snapshot_stores_both_views_and_points_at_them():
    _snap()
    (s,) = _snapshots()
    assert s["current_sha256"] == fingerprint("NDA with Acme Ltd")[1]
    assert s["original_sha256"] == fingerprint("NDA with [__]")[1]
    assert {v["text"] for v in _versions()} == {"NDA with Acme Ltd", "NDA with [__]"}
    assert (s["office_platform"], s["backend_build"]) == ("Mac", "b1")


def test_tracked_changes_are_capped_and_marked_cut():
    """Mutation: trust the pane's caps."""
    long = {"type": "Added", "author": "A", "date": "", "text": "x" * (ds.TRACKED_TEXT_CAP + 1)}
    _snap(tracked_changes=[long] + [{"type": "Deleted", "author": "B", "date": "", "text": "y"}]
          * ds.TRACKED_CAP, tracked_total=ds.TRACKED_CAP + 1)
    (s,) = _snapshots()
    assert len(s["tracked_changes"]) == ds.TRACKED_CAP and s["tracked_total"] == ds.TRACKED_CAP + 1
    assert len(s["tracked_changes"][0]["text"]) == ds.TRACKED_TEXT_CAP
    assert s["tracked_changes"][0]["cut"] is True and s["tracked_changes"][1]["cut"] is False


def test_a_total_below_what_arrived_is_raised_and_unreadable_stays_minus_one():
    _snap(tracked_total=0)
    _snap(tracked_changes=[], tracked_total=-1)
    first, second = _snapshots()
    assert first["tracked_total"] == 1
    assert second["tracked_total"] == -1 and second["tracked_changes"] == []


def test_a_lone_surrogate_or_a_nul_in_a_tracked_change_does_not_lose_the_capture():
    """Review Focus 1: JSONB refuses both. Mutation: store the items without storable()."""
    _snap(tracked_changes=[{"type": "Added", "author": "J\ud83d", "date": "", "text": "a\x00b"}])
    (s,) = _snapshots()
    assert (s["tracked_changes"][0]["author"], s["tracked_changes"][0]["text"]) == ("J?", "ab")


def test_an_unreadable_capture_keeps_no_list_even_when_one_arrives():
    """The test above sends an empty list with -1, so it cannot tell "keeps no
    list" from "had none". Mutation: store the list whatever the total."""
    _snap(tracked_changes=[{"type": "Added", "author": "A", "date": "", "text": "x"}],
          tracked_total=-1)
    (s,) = _snapshots()
    assert s["tracked_total"] == -1 and s["tracked_changes"] == []


def test_every_text_field_of_a_tracked_change_is_cleaned_and_bounded():
    """Author and text are pinned above; type and date are client-controlled too.
    Mutation: drop storable() or the bound from any one of the three fields."""
    _snap(tracked_changes=[{"type": "T\x00" + "t" * 500, "author": "a" * 500,
                            "date": "2026\ud83d" + "d" * 500, "text": "x"}])
    (change,) = _snapshots()[0]["tracked_changes"]
    assert [len(change[k]) for k in ("type", "author", "date")] == [ds._TRACKED_FIELD_CAP] * 3
    assert change["type"].startswith("Tt") and change["date"].startswith("2026?d")


def test_a_change_the_pane_already_cut_stays_marked_cut():
    """The pane caps a text at the same 2,000 and says so; what arrives is no
    longer than the cap, so only its flag tells a reader the text is partial.
    Mutation: decide `cut` from the length alone."""
    _snap(tracked_changes=[{"type": "Added", "author": "A", "date": "",
                            "text": "x" * ds.TRACKED_TEXT_CAP, "cut": True}])
    (s,) = _snapshots()
    assert s["tracked_changes"][0]["cut"] is True


def test_each_stored_view_names_the_attorney_who_sent_it_and_that_it_came_from_the_pane():
    """The erase-by-attorney command finds a capture's texts by this column
    (spec D9). Mutation: store the views without the attorney, or as a turn's."""
    _snap()
    assert {(v["source"], v["attorney_id"]) for v in _versions()} == {("pane", "atty-1")}
