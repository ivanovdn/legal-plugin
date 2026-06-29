"""document_id resolution — stable across body redlines, distinct across docs."""
from memory.document_id import resolve_document_id

_PREAMBLE = (
    "STATEMENT OF WORK\n\nThis SOW is entered into by Acme Corp and Globex LLC "
    "pursuant to the Master Services Agreement dated 2025-01-01.\n\n"
)


def test_same_preamble_same_id():
    a = resolve_document_id(_PREAMBLE + "1. Scope: build a website.")
    b = resolve_document_id(_PREAMBLE + "1. Scope: build a website. 2. Added clause.")
    assert a == b  # body redlines below the preamble must not change the id


def test_different_preamble_different_id():
    other = resolve_document_id("MUTUAL NDA\n\nBetween Foo Inc and Bar Ltd.\n\nbody")
    assert resolve_document_id(_PREAMBLE + "body") != other


def test_normalization_ignores_whitespace_and_case():
    a = resolve_document_id("Statement   of\tWork  between A and B")
    b = resolve_document_id("statement of work between a and b")
    assert a == b


def test_empty_text_returns_empty_id():
    assert resolve_document_id("") == ""
    assert resolve_document_id("   \n\t ") == ""
