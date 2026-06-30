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
