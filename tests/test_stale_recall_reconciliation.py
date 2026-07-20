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
    state = {"document_id": "d1"}
    block = lr._load_prior_review_block(state, "Signed by: Suzy Quatro\n")
    assert "PRIOR REVIEW" in block
    assert "MC-1" in block                # falls back to the raw review, unchanged
    assert "memory_degraded" not in state   # a reconcile error must NOT flag memory degraded


def test_multi_marker_bundled_span_not_dropped_when_partially_filled():
    review = (
        "# Red and Missing Context Items\n"
        "| MC-1 | Sig | `Signed by: [__] / Title: [__] / for and on behalf of [__]` unfilled | Missing Context |\n"
    )
    doc = "Signed by: John Smith / Title: [__] / for and on behalf of [__]\n"  # only 'Signed by' filled
    out, filled = _reconcile_review_with_doc(review, doc)
    assert "MC-1" in out          # bundled multi-field span left intact (conservative)
    assert filled == []           # nothing dropped


def test_note_lists_only_tokens_whose_rows_were_dropped():
    review = (
        "# Red and Missing Context Items\n"
        "| MC-1 | Both | `Signed by: [__]` and `Witness: [__]` unfilled | Missing Context |\n"
    )
    doc = "Signed by: Suzy Quatro\nWitness: [__]\n"  # signed filled, witness still blank -> row KEPT
    out, filled = _reconcile_review_with_doc(review, doc)
    assert "MC-1" in out                  # row kept (witness still blank)
    assert filled == []                   # nothing dropped -> no over-claim
    assert "Auto-reconciled" not in out   # no note when nothing was dropped


def test_surviving_blocker_count_counts_red_and_missing_context():
    md = (
        "# Key Findings\n"
        "| Issue ID | Clause | Rating | Issue |\n"
        "| --- | --- | --- | --- |\n"
        "| R-1 | Indemnity | Red | broad indemnity |\n"
        "| MC-1 | Signature | Missing Context | block blank |\n"
        "| Y-1 | Term | Yellow | auto-renew |\n"
        "| G-1 | Law | Green | fine |\n"
    )
    assert lr._surviving_blocker_count(md) == 2   # Red + Missing Context only


def test_surviving_blocker_count_accepts_risk_header_and_mc_spellings():
    md = (
        "# Key Findings\n"
        "| ID | Risk | Issue |\n"
        "| -- | -- | -- |\n"
        "| A | missing-context | x |\n"
        "| B | RED | y |\n"
    )
    assert lr._surviving_blocker_count(md) == 2   # "Risk" header + odd MC spelling + case


def test_surviving_blocker_count_none_when_no_key_findings():
    assert lr._surviving_blocker_count("# Review Summary\nAll clear.\n") is None


def test_surviving_blocker_count_none_when_no_rating_column():
    md = (
        "# Key Findings\n"
        "| Issue ID | Clause | Issue |\n"
        "| -- | -- | -- |\n"
        "| A | Indemnity | broad |\n"
    )
    assert lr._surviving_blocker_count(md) is None


# --- _reconcile_gate_verdict (unit) ---

def test_gate_neutralized_when_no_blockers_survive():
    review = (
        "# Key Findings\n"
        "| Issue ID | Clause / section | Rating | Issue | Required action | Owner |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| Y-1 | Term | Yellow | auto-renew 3y | Flag | Legal |\n"   # non-blocker survivor
        "# No Signature Checklist Result\n"
        "Overall status: Do not send for signature\n"
        "Blocking items: `Signed by: [__]` unfilled; `[Legal Name]` blank\n"
    )
    out = lr._reconcile_gate_verdict(review, ["Signed by: [__]", "[Legal Name]"])
    assert "PENDING RE-REVIEW" in out
    assert "Do not send for signature" not in out          # verdict rewritten
    assert "Reconciled:" in out                            # correction note added
    assert "ready for signature" not in out.lower()        # never green-lights
    assert "signature may proceed" not in out.lower()
    assert "Blocking items:" in out                        # blocking text preserved


def test_gate_annotated_only_when_blocker_survives():
    review = (
        "# Key Findings\n"
        "| Issue ID | Clause / section | Rating | Issue | Required action | Owner |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| R-1 | Indemnity | Red | broad indemnity | Escalate | Legal |\n"  # surviving blocker
        "# No Signature Checklist Result\n"
        "Overall status: Do not send for signature\n"
        "Blocking items: `Signed by: [__]` unfilled\n"
    )
    out = lr._reconcile_gate_verdict(review, ["Signed by: [__]"])
    assert "Reconciled:" in out                            # note added
    assert "Do not send for signature" in out             # verdict retained
    assert "PENDING RE-REVIEW" not in out                  # not downgraded


def test_gate_unchanged_when_not_citing_dropped_token():
    review = (
        "# No Signature Checklist Result\n"
        "Overall status: Do not send for signature\n"
        "Blocking items: `Effective Date: [__]` blank\n"
    )
    out = lr._reconcile_gate_verdict(review, ["Signed by: [__]"])  # token not in gate
    assert out == review


def test_gate_unchanged_when_no_gate_section():
    review = (
        "# Key Findings\n"
        "| Issue ID | Rating |\n| --- | --- |\n| R-1 | Red |\n"
    )
    out = lr._reconcile_gate_verdict(review, ["Signed by: [__]"])
    assert out == review


def test_gate_annotate_when_neutralize_eligible_but_no_status_line():
    review = (
        "# Key Findings\n"
        "| Issue ID | Rating |\n| --- | --- |\n| G-1 | Green |\n"   # 0 blockers -> eligible
        "# No Signature Checklist Result\n"
        "Blocking items: `Signed by: [__]` unfilled\n"             # no 'Overall status:' line
    )
    out = lr._reconcile_gate_verdict(review, ["Signed by: [__]"])
    assert "Reconciled:" in out                            # note inserted
    assert "PENDING RE-REVIEW" not in out                  # nothing to downgrade -> annotate


def test_gate_no_downgrade_when_key_findings_absent():
    review = (
        "# No Signature Checklist Result\n"
        "Overall status: Do not send for signature\n"
        "Blocking items: `Signed by: [__]` unfilled\n"
    )
    out = lr._reconcile_gate_verdict(review, ["Signed by: [__]"])
    assert "Reconciled:" in out
    assert "Do not send for signature" in out             # None count -> conservative
    assert "PENDING RE-REVIEW" not in out


def test_gate_empty_dropped_tokens_byte_identical():
    review = "# No Signature Checklist Result\nOverall status: Do not send for signature\n"
    assert lr._reconcile_gate_verdict(review, []) == review


# --- _reconcile_review_with_doc (end-to-end) ---

def test_gate_blocking_items_line_survives_row_drop():
    review = (
        "# Red and Missing Context Items\n"
        "| MC-1 | Signature | `Signed by: [__]` unfilled | Missing Context |\n"
        "# No Signature Checklist Result\n"
        "Overall status: Do not send for signature\n"
        "Blocking items: `Signed by: [__]` unfilled\n"
    )
    doc = "Signed by: Suzy Quatro\n"
    out, dropped = _reconcile_review_with_doc(review, doc)
    assert "MC-1" not in out                                       # finding row dropped
    assert "Blocking items: `Signed by: [__]`" in out             # gate line NOT row-dropped
    assert dropped == ["Signed by: [__]"]


def test_reconcile_end_to_end_neutralizes_gate():
    review = (
        "# Key Findings\n"
        "| Issue ID | Clause / section | Rating | Issue | Required action | Owner |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| MC-1 | Signature | Missing Context | `Signed by: [__]` unfilled | Fill | Legal |\n"
        "# No Signature Checklist Result\n"
        "Overall status: Do not send for signature\n"
        "Blocking items: `Signed by: [__]` unfilled\n"
    )
    doc = "Signed by: Suzy Quatro\n"
    out, dropped = _reconcile_review_with_doc(review, doc)
    assert "MC-1" not in out                       # Key Findings placeholder row dropped
    assert "Auto-reconciled" in out                # top note present
    assert "PENDING RE-REVIEW" in out              # gate neutralized (0 blockers survive)
    assert "ready for signature" not in out.lower()
    assert "signature may proceed" not in out.lower()


def test_reconcile_end_to_end_annotates_when_blocker_survives():
    review = (
        "# Key Findings\n"
        "| Issue ID | Clause / section | Rating | Issue | Required action | Owner |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| R-1 | Indemnity | Red | broad indemnity | Escalate | Legal |\n"
        "| MC-1 | Signature | Missing Context | `Signed by: [__]` unfilled | Fill | Legal |\n"
        "# No Signature Checklist Result\n"
        "Overall status: Do not send for signature\n"
        "Blocking items: `Signed by: [__]` unfilled\n"
    )
    doc = "Signed by: Suzy Quatro\n"
    out, dropped = _reconcile_review_with_doc(review, doc)
    assert "MC-1" not in out                        # placeholder finding dropped
    assert "R-1" in out                             # substantive Red finding kept
    assert "Do not send for signature" in out       # verdict retained (blocker survives)
    assert "PENDING RE-REVIEW" not in out


def test_surviving_blocker_count_strips_rating_emphasis():
    md = (
        "# Key Findings\n"
        "| ID | Rating | Issue |\n"
        "| -- | -- | -- |\n"
        "| A | **Red** | bold red |\n"
        "| B | `Missing Context` | backtick mc |\n"
        "| C | missing_context | underscore spelling still counts |\n"
        "| D | *Yellow* | italic non-blocker |\n"
    )
    assert lr._surviving_blocker_count(md) == 3   # A, B, C blockers; D (Yellow) not


def test_end_to_end_bold_rating_blocker_prevents_neutralize():
    review = (
        "# Key Findings\n"
        "| Issue ID | Clause / section | Rating | Issue | Required action | Owner |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| R-1 | Indemnity | **Red** | uncapped indemnity | Escalate | Legal |\n"
        "| MC-1 | Signature | Missing Context | `Signed by: [__]` unfilled | Fill | Legal |\n"
        "# No Signature Checklist Result\n"
        "Overall status: Do not send for signature\n"
        "Blocking items: `Signed by: [__]` unfilled\n"
    )
    doc = "Signed by: Suzy Quatro\n"
    out, dropped = _reconcile_review_with_doc(review, doc)
    assert "MC-1" not in out                       # placeholder dropped
    assert "R-1" in out                            # bold Red survives
    assert "Do not send for signature" in out      # verdict retained (real blocker present)
    assert "PENDING RE-REVIEW" not in out          # NOT neutralized despite bold rating
    assert "Reconciled:" in out                      # gate annotated
