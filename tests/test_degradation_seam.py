"""The degradation vocabulary and the four seam helpers."""
from __future__ import annotations


def test_vocabulary_is_closed_and_partitioned():
    import observability.degradations as D

    assert len(D.FAILED_REASONS) == 8
    assert len(D.ANNOUNCED_REASONS) == 7
    assert len(D.SILENT_REASONS) == 7
    assert len(D.ALL_REASONS) == 22
    # The three classes must not overlap — a code is failed, announced or
    # silent, never two of them.
    assert D.FAILED_REASONS & D.ANNOUNCED_REASONS == frozenset()
    assert D.FAILED_REASONS & D.SILENT_REASONS == frozenset()
    assert D.ANNOUNCED_REASONS & D.SILENT_REASONS == frozenset()
    assert D.ALL_REASONS == D.FAILED_REASONS | D.ANNOUNCED_REASONS | D.SILENT_REASONS


def test_every_constant_value_matches_its_name():
    """A constant whose value drifts from its name makes greps lie."""
    import observability.degradations as D

    for attr in dir(D):
        if attr.isupper() and isinstance(getattr(D, attr), str):
            assert getattr(D, attr) == attr.lower(), f"{attr} value does not match its name"


def test_derive_outcome_maps_reasons_to_the_three_states():
    from observability.degradations import (
        AUDIT_WRITE_FAILED, CHAT_GROUNDING_FAILED, GRAPH_INVOKE_FAILED, derive_outcome,
    )

    assert derive_outcome([]) == "ok"
    assert derive_outcome([CHAT_GROUNDING_FAILED]) == "degraded"
    assert derive_outcome([AUDIT_WRITE_FAILED]) == "degraded"
    assert derive_outcome([GRAPH_INVOKE_FAILED]) == "failed"
    # A failure anywhere in the list wins over any number of degradations.
    assert derive_outcome([CHAT_GROUNDING_FAILED, GRAPH_INVOKE_FAILED]) == "failed"
