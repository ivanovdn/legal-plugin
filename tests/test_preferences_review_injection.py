import importlib

from config import get_settings
from memory.preferences import save_preferences
from skills.contract_review import contract_review
from skills.contract_review.contract_review import _OUTPUT_CONSTRAINTS

# The package's __init__.py re-exports the `contract_review` FUNCTION under the
# name `contract_review` (shadowing the submodule attribute Python's import
# machinery would otherwise set on the package). `import skills.contract_review
# .contract_review as cr` therefore also resolves to the function, not the
# module — so module-level constants (`_OUTPUT_CONSTRAINTS`,
# `_MSA_COMPARISON_DIRECTIVE`) must be reached via the real module object in
# sys.modules, not package-attribute traversal.
cr = importlib.import_module("skills.contract_review.contract_review")


def test_preferences_after_playbook_before_output_constraints(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "preferences_dir", str(tmp_path))
    monkeypatch.setattr(s, "preferences_enabled", True)
    save_preferences(str(tmp_path), "atty-5", "- Always flag uncapped indemnity.")

    state = {
        "request": "Review this contract.",
        "user_id": "atty-5",
        "uploaded_docs": [{"text": "MUTUAL NON-DISCLOSURE AGREEMENT\n\n1. Term ..."}],
        "filters": {},
    }
    out = contract_review(state)
    systems = [m["content"] for m in out["messages"] if m["role"] == "system"]
    pref_idx = next(i for i, c in enumerate(systems) if "ATTORNEY PREFERENCES" in c)
    oc_idx = next(i for i, c in enumerate(systems) if c == _OUTPUT_CONSTRAINTS)
    assert 0 < pref_idx < oc_idx           # after playbook (index 0), before output constraints
    assert out["messages"][-1]["role"] == "user"   # prefs never last


def test_no_preferences_when_empty(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "preferences_dir", str(tmp_path))
    monkeypatch.setattr(s, "preferences_enabled", True)
    state = {
        "request": "Review this contract.",
        "user_id": "atty-empty",
        "uploaded_docs": [{"text": "MUTUAL NON-DISCLOSURE AGREEMENT"}],
        "filters": {},
    }
    out = contract_review(state)
    assert all("ATTORNEY PREFERENCES" not in m["content"] for m in out["messages"])


def test_msa_directive_stays_last_with_prefs_before_output_constraints(tmp_path, monkeypatch):
    """Legal-safety regression: on the SOW+MSA path the order must still be
    playbook -> prefs -> _OUTPUT_CONSTRAINTS -> _MSA_COMPARISON_DIRECTIVE (last).
    The other tests in this file use NDA fixtures (no MSA), so this invariant —
    prefs landing before the MSA directive, and the MSA directive remaining the
    final system message — was previously untested."""
    s = get_settings()
    monkeypatch.setattr(s, "preferences_dir", str(tmp_path))
    monkeypatch.setattr(s, "preferences_enabled", True)
    save_preferences(str(tmp_path), "atty-sow", "- Always flag uncapped indemnity.")

    # NOTE: a string-form monkeypatch target here ("skills.contract_review.
    # contract_review.attach_parent_msa") resolves incorrectly: pytest's
    # dotted-path resolver walks the path via getattr(), and
    # `skills.contract_review` (the package) already has an attribute named
    # `contract_review` — the re-exported FUNCTION, not the submodule (see the
    # note above `cr = importlib.import_module(...)`). getattr succeeds on the
    # function, so the resolver never falls back to importing the real
    # submodule, and `attach_parent_msa` is looked up on the function object
    # and raises AttributeError. Patching the real module object directly
    # (`cr`, obtained via importlib) sidesteps that resolution bug.
    monkeypatch.setattr(
        cr, "attach_parent_msa", lambda text, client_id, max_chars: ("Model MSA", "MSA BODY TEXT")
    )

    state = {
        "request": "Review this contract.",
        "user_id": "atty-sow",
        "uploaded_docs": [{"text": "STATEMENT OF WORK\n\n1. Services ..."}],
        "filters": {"client_id": "internal"},
    }
    out = cr.contract_review(state)
    systems = [m["content"] for m in out["messages"] if m["role"] == "system"]

    pref_idx = systems.index(next(c for c in systems if "ATTORNEY PREFERENCES" in c))
    oc_idx = systems.index(cr._OUTPUT_CONSTRAINTS)
    msa_idx = systems.index(cr._MSA_COMPARISON_DIRECTIVE)

    assert pref_idx < oc_idx < msa_idx
    assert systems[-1] == cr._MSA_COMPARISON_DIRECTIVE
    assert out["messages"][-1]["role"] == "user"
