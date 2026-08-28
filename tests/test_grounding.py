# tests/test_grounding.py
"""Shared grounding helpers used by both contract_review and the chat path."""
import skills.grounding as g


def test_detect_sow():
    ctype, ambiguous = g.detect_contract_type("STATEMENT OF WORK\n\nbody about the project")
    assert ctype == "sow"
    assert ambiguous is False


def test_detect_defaults_nda_when_ambiguous():
    ctype, ambiguous = g.detect_contract_type("Some text with no contract keywords at all.")
    assert ctype == "nda"
    assert ambiguous is True


def test_load_playbook_bundle_returns_text():
    bundle = g.load_playbook_bundle("sow")
    assert isinstance(bundle, str) and len(bundle) > 100


def test_attach_parent_msa_none_without_client(monkeypatch):
    monkeypatch.setattr(g, "get_parent_msa", lambda client_id: None)
    assert g.attach_parent_msa("SOW text", "", max_chars=1000) is None


def test_attach_parent_msa_truncates(monkeypatch):
    monkeypatch.setattr(g, "get_parent_msa", lambda client_id: ("Model MSA", "X" * 5000))
    title, text = g.attach_parent_msa("SOW text", "internal", max_chars=1000)
    assert title == "Model MSA"
    assert len(text) <= 1000 + 60   # truncation marker allowance
    assert "truncated" in text


def test_no_room_means_no_msa_rather_than_an_inverted_slice():
    """Both callers derive max_chars by subtraction, so both can go negative.

    Python does not complain: msa_text[:-33446] returns everything but the LAST
    33,446 characters — the head-slice inverted — and the truncation note would
    quote a negative size. Reachable today at the 90,000 test budget with a large
    document (84,859 + playbook 38,587 leaves -33,446).
    """
    called = []
    original = g.get_parent_msa
    g.get_parent_msa = lambda c: called.append(c)
    try:
        assert g.attach_parent_msa("SOW text", "internal", -33446) is None
        assert g.attach_parent_msa("SOW text", "internal", 0) is None
    finally:
        g.get_parent_msa = original
    assert called == [], "the store is not even queried when there is no room"
