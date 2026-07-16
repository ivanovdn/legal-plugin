"""Stale-recall reconciliation: drop placeholder findings the current doc proves are filled."""
import skills.legal_research as lr
from skills.legal_research import _reconcile_review_with_doc


def test_filled_placeholder_dropped_and_note_added():
    review = (
        "# Red and Missing Context Items\n"
        "| MC-1 | Signature | `Signed by: [__]` is unfilled | Missing Context |\n"
        "| MC-2 | Date | `Effective Date: [__]` is unfilled | Missing Context |\n"
    )
    doc = "Signed by: Suzy Quatro\nEffective Date: [__]\n"
    out, filled = _reconcile_review_with_doc(review, doc)
    assert "MC-1" not in out              # filled -> finding row dropped
    assert "MC-2" in out                  # still unfilled -> kept
    assert "Auto-reconciled" in out
    assert "Signed by: [__]" in filled
    assert "Effective Date: [__]" not in filled


def test_still_unfilled_kept_unchanged():
    review = "| MC-1 | Party | `[Legal Name]` blank | Missing Context |\n"
    doc = "Party: [Legal Name]\n"
    out, filled = _reconcile_review_with_doc(review, doc)
    assert out == review                  # nothing stale -> byte-identical
    assert filled == []


def test_generic_blank_disambiguation():
    review = (
        "| MC-1 | Sig | `Signed by: [__]` unfilled | Missing Context |\n"
        "| MC-2 | Witness | `Witness: [__]` unfilled | Missing Context |\n"
    )
    doc = "Signed by: Suzy Quatro\nWitness: [__]\n"   # first filled, second not
    out, filled = _reconcile_review_with_doc(review, doc)
    assert "MC-1" not in out
    assert "MC-2" in out
    assert filled == ["Signed by: [__]"]


def test_no_placeholders_returns_unchanged():
    review = "# Summary\nStandalone review, all clear.\n"
    doc = "Any document text.\n"
    out, filled = _reconcile_review_with_doc(review, doc)
    assert out == review
    assert filled == []


def test_substantive_finding_and_lowercase_bracket_untouched():
    review = (
        "| R-1 | Indemnity | Broad indemnity; add carve-outs [e.g. gross negligence] | Red |\n"
        "| MC-1 | Sig | `Signed by: [__]` unfilled | Missing Context |\n"
    )
    doc = "Signed by: Suzy Quatro\n"
    out, filled = _reconcile_review_with_doc(review, doc)
    assert "R-1" in out                   # substantive finding kept
    assert "[e.g. gross negligence]" in out   # lowercase bracket never a placeholder
    assert "MC-1" not in out              # placeholder filled -> dropped
    assert filled == ["Signed by: [__]"]


def test_source_tag_never_a_candidate():
    review = "| G-1 | Draft | `[Source: abc123]` heading tag | Green |\n"
    doc = "Contract text without that tag.\n"
    out, filled = _reconcile_review_with_doc(review, doc)
    assert "G-1" in out                   # not dropped
    assert filled == []


def test_normalization_tolerates_nbsp():
    review = "| MC-1 | Party | `[Legal Name]` blank | Missing Context |\n"
    doc = "Party: [Legal Name]\n"    # non-breaking space inside
    out, filled = _reconcile_review_with_doc(review, doc)
    assert "MC-1" in out                  # nbsp-normalized match -> not stale -> kept
    assert filled == []


def test_malformed_markdown_does_not_crash():
    review = "```\nunterminated `backtick and [Bracket without close\n| bad | row\n"
    doc = "whatever\n"
    out, filled = _reconcile_review_with_doc(review, doc)
    assert isinstance(out, str)
    assert isinstance(filled, list)


def test_recurring_label_across_contexts_drops_only_filled():
    review = (
        "# Red and Missing Context Items\n"
        "| MC-1 | Landlord | `Landlord: [Legal Name]` blank | Missing Context |\n"
        "| MC-2 | Tenant | `Tenant: [Legal Name]` blank | Missing Context |\n"
    )
    doc = "Landlord: Acme Corp\nTenant: [Legal Name]\n"   # landlord filled, tenant blank
    out, filled = _reconcile_review_with_doc(review, doc)
    assert "MC-1" not in out          # landlord filled -> dropped
    assert "MC-2" in out              # tenant blank -> kept
    assert filled == ["Landlord: [Legal Name]"]


_REVIEW_MD = (
    "# Red and Missing Context Items\n"
    "| MC-1 | Sig | `Signed by: [__]` unfilled | Missing Context |\n"
)


def _fake_latest(_db, _doc_id):
    return {"markdown": _REVIEW_MD, "timestamp": "t", "session_id": "s", "contract_type": "nda"}


def test_load_prior_review_block_injects_reconciled(monkeypatch):
    monkeypatch.setattr(lr, "load_latest_review", _fake_latest)
    block = lr._load_prior_review_block({"document_id": "d1"}, "Signed by: Suzy Quatro\n")
    assert "PRIOR REVIEW" in block
    assert "MC-1" not in block            # stale finding reconciled out
    assert "Auto-reconciled" in block


def test_load_prior_review_block_survives_reconcile_error(monkeypatch):
    monkeypatch.setattr(lr, "load_latest_review", _fake_latest)

    def _boom(_review, _doc):
        raise ValueError("reconcile bug")

    monkeypatch.setattr(lr, "_reconcile_review_with_doc", _boom)
    block = lr._load_prior_review_block({"document_id": "d1"}, "Signed by: Suzy Quatro\n")
    assert "PRIOR REVIEW" in block
    assert "MC-1" in block                # falls back to the raw review, unchanged
