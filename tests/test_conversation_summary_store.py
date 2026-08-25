"""Append-only conversation summary segments — write, windowed read, boundary."""
from memory.conversation_summary import append_segment, latest_to_id, load_segments


def test_append_and_load_round_trip():
    seg_id = append_segment("doc-1", "atty-1", 1, 10, "[#3 attorney] \"use Suzy Quatro\"")
    assert seg_id > 0
    segs = load_segments("doc-1", "atty-1", 3)
    assert len(segs) == 1
    assert segs[0]["from_id"] == 1
    assert segs[0]["to_id"] == 10
    assert segs[0]["content"] == "[#3 attorney] \"use Suzy Quatro\""
    assert segs[0]["id"] == seg_id


def test_load_segments_is_oldest_first_and_windowed():
    append_segment("doc-1", "atty-1", 1, 10, "first")
    append_segment("doc-1", "atty-1", 11, 20, "second")
    append_segment("doc-1", "atty-1", 21, 30, "third")
    # The window keeps the MOST RECENT n, but hands them back chronologically —
    # a summary read out of order would misrepresent what happened when.
    assert [s["content"] for s in load_segments("doc-1", "atty-1", 2)] == ["second", "third"]
    assert [s["content"] for s in load_segments("doc-1", "atty-1", 9)] == [
        "first", "second", "third",
    ]


def test_latest_to_id_is_the_boundary_across_all_segments():
    assert latest_to_id("doc-1", "atty-1") == 0
    append_segment("doc-1", "atty-1", 1, 10, "first")
    append_segment("doc-1", "atty-1", 11, 26, "second")
    # Deliberately NOT max(injected window) — rows 1..26 are all condensed, so
    # the verbatim floor must be 26 even when only one segment is injected.
    assert latest_to_id("doc-1", "atty-1") == 26


def test_per_attorney_and_per_document_isolation():
    append_segment("doc-1", "atty-1", 1, 10, "mine")
    append_segment("doc-1", "atty-2", 1, 10, "theirs")
    append_segment("doc-2", "atty-1", 1, 10, "other doc")
    assert [s["content"] for s in load_segments("doc-1", "atty-1", 9)] == ["mine"]
    assert [s["content"] for s in load_segments("doc-1", "atty-2", 9)] == ["theirs"]
    assert [s["content"] for s in load_segments("doc-2", "atty-1", 9)] == ["other doc"]
    assert latest_to_id("doc-1", "atty-2") == 10


def test_empty_ids_and_nonpositive_window_return_empty():
    append_segment("doc-1", "atty-1", 1, 10, "seg")
    assert load_segments("", "atty-1", 3) == []
    assert load_segments("doc-1", "", 3) == []
    # Postgres, unlike SQLite, rejects a negative LIMIT — short-circuit before
    # the query (same hazard as conversation_store.load_recent).
    assert load_segments("doc-1", "atty-1", 0) == []
    assert load_segments("doc-1", "atty-1", -1) == []
    assert latest_to_id("", "atty-1") == 0
