"""What a turn ran with, beyond the code: the backend's settings and the model
on the inference server. Both go on the turn_log row.

`backend_build` names the code. The VM's .env (context size, review thinking,
grounding switches) and the weights behind `qwen3.6:latest` on Spark — a
shared server another team can `ollama pull` on — can each change mid-pilot
without a deploy. Without these two facts, two turns cannot be compared.
"""
from __future__ import annotations

import logging
import threading
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

from config import Settings

logger = logging.getLogger(__name__)

# Every Settings field is on exactly one of these lists — tests/test_runtime_facts.py
# fails on a field that is on neither, so a credential added later is never
# recorded by default. NEVER_RECORDED holds anything that can carry a secret.
NEVER_RECORDED: frozenset[str] = frozenset({
    "database_url", "redis_url", "otel_exporter_otlp_headers",
})
RECORDED: frozenset[str] = frozenset({
    "ollama_base_url", "llm_model", "embedding_model", "qdrant_vector_dim", "qdrant_url",
    "reranker_enabled", "reranker_backend", "reranker_url", "reranker_model",
    "reranker_top_n", "reranker_candidates", "reranker_query_template",
    "reranker_instruction", "retrieval_top_k", "min_confidence_score",
    "hybrid_vector_candidates", "hybrid_bm25_candidates", "embedding_query_prefix",
    "embedding_passage_prefix", "chunk_min_tokens", "chunk_max_tokens",
    "escalation_ticket_prefix", "bm25_enabled", "checkpointer_enabled",
    "interrupt_enabled", "chat_history_n_turns", "chat_history_trim_chars",
    "max_review_iterations", "checkpoint_ttl_seconds", "ollama_num_ctx",
    "ollama_num_predict_chat", "ollama_num_predict_review", "ollama_think_review",
    "chat_context_max_chars", "est_chars_per_token", "chat_conditional_grounding",
    "msa_max_chars", "conversation_store_enabled", "conversation_max_messages",
    "compaction_enabled", "compaction_keep_recent_messages", "compaction_min_quotes",
    "compaction_warn_pct", "compaction_max_quotes", "compaction_max_injected_segments",
    "compaction_auto", "compaction_auto_min_chars", "preferences_enabled",
    "preferences_dir", "preferences_max_chars", "feedback_enabled",
    "feedback_snapshot_max_chars", "document_capture_enabled",
    "sso_enabled", "sso_tenant_id", "sso_client_id",
    "sso_issuer", "sso_jwks_url", "otel_exporter_otlp_endpoint", "otel_service_name",
    "tracing_enabled", "otel_instrument_redis", "api_port", "chainlit_port",
    "db_pool_timeout", "log_level", "build_sha",
})


def _without_credentials(url: str) -> str:
    """Scheme, host, port and path only — no `user:pass@`, query or fragment.
    No URL carries a credential today; one pasted into .env must still not
    reach the table."""
    if not url:
        return url
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc.rpartition("@")[2], parts.path, "", ""))


def settings_snapshot(settings: Settings) -> dict:
    """The recorded settings as plain JSON values, URLs stripped of credentials."""
    snap = settings.model_dump(mode="json", include=set(RECORDED))
    for key, value in snap.items():
        if key.endswith(("_url", "_endpoint")) and isinstance(value, str):
            snap[key] = _without_credentials(value)
    return snap


# Asked at most once a minute: the answer barely changes, and a Spark outage
# must not put a timeout in front of every turn. Failures are cached as well,
# and only ONE caller refreshes an expired answer — the rest get the last known
# one (empty if none) at once rather than each waiting out a timeout. So a model
# pulled on the server shows up on turns up to a minute after the pull.
_TTL_S = 60.0
_TIMEOUT = httpx.Timeout(2.0, connect=1.0)
_EMPTY = {"model_digest": "", "ollama_version": ""}
_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_refreshing: set[tuple[str, str]] = set()
_lock = threading.Lock()


def reset_ollama_cache() -> None:
    with _lock:
        _cache.clear()
        _refreshing.clear()


def _with_tag(model: str) -> str:
    return model if ":" in model else f"{model}:latest"


def _ask(base_url: str, model: str) -> dict:
    try:
        tags = httpx.get(f"{base_url}/api/tags", timeout=_TIMEOUT)
        tags.raise_for_status()
        wanted = _with_tag(model)
        digest = next(
            (m.get("digest") for m in tags.json().get("models", [])
             if _with_tag(m.get("name") or "") == wanted),
            "",
        )
        version = httpx.get(f"{base_url}/api/version", timeout=_TIMEOUT)
        version.raise_for_status()
        # Both columns are NOT NULL: a JSON null here would fail the row's write.
        return {"model_digest": str(digest or ""),
                "ollama_version": str(version.json().get("version") or "")}
    except Exception as e:  # noqa: BLE001 — a fact we could not get is empty, never a broken turn
        logger.warning("[runtime_facts] could not read the model server at %s: %s", base_url, e)
        return dict(_EMPTY)


def ollama_facts(base_url: str, model: str) -> dict:
    """{"model_digest", "ollama_version"} for `model` on `base_url`; empty
    strings when the server does not answer. Never raises."""
    key = (base_url, model)
    with _lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < _TTL_S:
            return dict(hit[1])
        if key in _refreshing:
            return dict(hit[1] if hit else _EMPTY)
        _refreshing.add(key)
    try:
        facts = _ask(base_url, model)
        with _lock:
            _cache[key] = (time.monotonic(), facts)
        return dict(facts)
    finally:
        with _lock:
            _refreshing.discard(key)
