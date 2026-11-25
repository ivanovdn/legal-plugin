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


def test_msa_reference_quotes_the_sentence_naming_the_msa():
    text = ('STATEMENT OF WORK\nPreamble. This SOW is made pursuant to and subject to the Master '
            'Services Agreement dated 1 May 2026 (the "MSA"). Next sentence.')
    assert g.msa_reference(text) == (
        'This SOW is made pursuant to and subject to the Master Services Agreement dated 1 May 2026 (the "MSA").'
    )


def test_msa_reference_prefers_the_full_name_over_a_bare_msa():
    text = "Fees are payable under the MSA.\nThis SOW is governed by the Master Service Agreement dated [Date]."
    assert g.msa_reference(text) == "This SOW is governed by the Master Service Agreement dated [Date]."


def test_msa_reference_falls_back_to_a_bare_msa():
    assert g.msa_reference("Scope.\nAll terms of the MSA apply to this SOW.\nTeam.") == (
        "All terms of the MSA apply to this SOW."
    )


def test_msa_reference_is_empty_when_no_msa_is_named():
    assert g.msa_reference("STATEMENT OF WORK\nThe parties agree on scope. Smsa is not a word.") == ""


def test_msa_reference_ignores_an_msa_named_only_past_the_opening():
    # Pilot turn e9a92775's standalone SOW first says "this Master Service
    # Agreement" deep in a liability clause copied from an MSA; that is not the
    # SOW naming a governing MSA.
    body = "STATEMENT OF WORK No. 1\nThis Statement of Work is entered into by the Parties.\n" + "Scope. " * 400
    assert g.msa_reference(body + "LIABILITY UNDER THIS MASTER SERVICE AGREEMENT SHALL NOT EXCEED THE FEES.") == ""


def test_msa_reference_ends_at_a_line_break_and_collapses_whitespace():
    text = "subject to the Master Service   Agreement dated\t[Date]\r\nAPPENDIX 1"
    assert g.msa_reference(text) == "subject to the Master Service Agreement dated [Date]"


def test_msa_reference_is_capped_at_a_word_boundary():
    text = "This SOW is subject to the Master Service Agreement " + "and its appendices " * 40 + "."
    quote = g.msa_reference(text)
    assert quote.endswith(" …")
    assert len(quote) <= g._MSA_REFERENCE_MAX_CHARS + 2
    assert quote.startswith("This SOW is subject to the Master Service Agreement and its")


def test_attach_parent_msa_none_without_client(monkeypatch):
    monkeypatch.setattr(g, "get_parent_msa", lambda client_id: None)
    assert g.attach_parent_msa("SOW text", "", max_chars=1000) is None


def test_attach_parent_msa_truncates(monkeypatch):
    monkeypatch.setattr(g, "get_parent_msa", lambda client_id: ("Model MSA", "X" * 5000))
    title, text = g.attach_parent_msa("SOW text", "internal", max_chars=1000)
    assert title == "Model MSA"
    assert len(text) <= 1000 + 60   # truncation marker allowance
    assert "truncated" in text
