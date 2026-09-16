"""Closed vocabulary of degradation reason codes.

Free-text reasons fragment into things you cannot filter on. Every call to
observability.spans.mark_failed / record_degradation passes one of these
constants — never a string literal — and scripts/check_degradation_vocabulary.py
asserts the declared set and the used set agree in BOTH directions:

  - declared but never used  -> a site someone forgot to wire
  - used but not declared    -> a typo silently creating a new category

A unit test cannot catch either: a green test_record_degradation_emits_event
passes with 21 of 22 sites unwired, because the call sites simply do not exist.

Class 4 (telemetry self-catch) deliberately has NO codes. Tracing reporting its
own best-effort catches as application degradations is how a dashboard becomes
decoration. See docs/superpowers/specs/2026-09-16-trace-coverage-design.md.
"""
from __future__ import annotations

# --- Class 1: FAILED -------------------------------------------------------
# The attorney got no answer, or an error string as the answer.
# Wired with mark_failed() -> span status ERROR.
LLM_CALL_FAILED = "llm_call_failed"
LEGAL_RESEARCH_FAILED = "legal_research_failed"
CONTRACT_GENERATION_FAILED = "contract_generation_failed"
COMPACTION_FAILED = "compaction_failed"
GRAPH_INVOKE_FAILED = "graph_invoke_failed"
STATELESS_FALLBACK_FAILED = "stateless_fallback_failed"
RESUME_STATE_LOAD_FAILED = "resume_state_load_failed"
RESUME_FAILED = "resume_failed"

FAILED_REASONS = frozenset({
    LLM_CALL_FAILED,
    LEGAL_RESEARCH_FAILED,
    CONTRACT_GENERATION_FAILED,
    COMPACTION_FAILED,
    GRAPH_INVOKE_FAILED,
    STATELESS_FALLBACK_FAILED,
    RESUME_STATE_LOAD_FAILED,
    RESUME_FAILED,
})

# --- Class 2: ANNOUNCED ----------------------------------------------------
# Answer lands, quality is worse, and the attorney IS told (banner / notice).
# Wired with record_degradation(announced=True). Never changes span status.
CHECKPOINTER_UNAVAILABLE = "checkpointer_unavailable"
AUDIT_WRITE_FAILED = "audit_write_failed"
REVIEW_PERSIST_FAILED = "review_persist_failed"
PRIOR_REVIEW_LOAD_FAILED = "prior_review_load_failed"
SUMMARY_LOAD_FAILED = "summary_load_failed"
PRIOR_CONVERSATION_LOAD_FAILED = "prior_conversation_load_failed"
CONTEXT_TRUNCATED = "context_truncated"

ANNOUNCED_REASONS = frozenset({
    CHECKPOINTER_UNAVAILABLE,
    AUDIT_WRITE_FAILED,
    REVIEW_PERSIST_FAILED,
    PRIOR_REVIEW_LOAD_FAILED,
    SUMMARY_LOAD_FAILED,
    PRIOR_CONVERSATION_LOAD_FAILED,
    CONTEXT_TRUNCATED,
})

# --- Class 3: SILENT -------------------------------------------------------
# Answer lands, quality is quietly worse, and NOBODY is told. This class is the
# reason this work exists. Wired with record_degradation(announced=False).
CHAT_GROUNDING_FAILED = "chat_grounding_failed"
REVIEW_RECONCILIATION_FAILED = "review_reconciliation_failed"
COMPRESSIBLE_HISTORY_READ_FAILED = "compressible_history_read_failed"
PREFERENCES_LOAD_FAILED = "preferences_load_failed"
MSA_LOOKUP_FAILED = "msa_lookup_failed"
INTENT_CLASSIFICATION_FAILED = "intent_classification_failed"
PLANNING_FAILED = "planning_failed"

SILENT_REASONS = frozenset({
    CHAT_GROUNDING_FAILED,
    REVIEW_RECONCILIATION_FAILED,
    COMPRESSIBLE_HISTORY_READ_FAILED,
    PREFERENCES_LOAD_FAILED,
    MSA_LOOKUP_FAILED,
    INTENT_CLASSIFICATION_FAILED,
    PLANNING_FAILED,
})

ALL_REASONS = FAILED_REASONS | ANNOUNCED_REASONS | SILENT_REASONS

# --- Outcomes ----------------------------------------------------------------
# The three values app.outcome can take, per request root. Named, not typed
# out at each of the (now four, soon seven — Tasks 8/13) call sites: a typo on
# either side of `set_outcome(...)`/`derive_outcome(...)` would silently skip
# the root ERROR status, which is the one thing this seam exists to make
# reliable.
OUTCOME_OK = "ok"
OUTCOME_DEGRADED = "degraded"
OUTCOME_FAILED = "failed"


def derive_outcome(reasons: list[str]) -> str:
    """ok | degraded | failed, from the reason codes recorded this request.

    ONE source, read back — not recomputed. The reason accumulator is complete
    by construction: every PRODUCER of a report flag (memory_degraded,
    context_truncated, review_persist_error) also records a reason code, so it
    already sees everything the pane sees. OR-ing the report flags in as well
    would create two sources that can disagree.

    "Producer", not "site", deliberately: context_truncated has two — the
    detect-only one in llm_caller and the one in _cap_chat_context that
    actually cuts — and only the first was wired at first. The vocabulary gate
    counts CODES, not producers, so it cannot see a second producer of an
    already-used code. Check this premise per flag-setting line, not per code.
    """
    if FAILED_REASONS & set(reasons):
        return OUTCOME_FAILED
    if reasons:
        return OUTCOME_DEGRADED
    return OUTCOME_OK
