# tests/test_config.py
import os
import pytest

from config import Settings, get_settings


def test_config_loads_from_env(monkeypatch):
    """Config loads all fields from environment variables."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("LLM_MODEL", "llama3")
    monkeypatch.setenv("EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    monkeypatch.setenv("REDIS_URL", "redis://:myredissecret@localhost:6379")
    monkeypatch.setenv("RERANKER_ENABLED", "true")
    monkeypatch.setenv("RERANKER_BACKEND", "llama-cpp")
    monkeypatch.setenv("RERANKER_URL", "http://localhost:8081/v1/rerank")
    monkeypatch.setenv("RERANKER_MODEL", "bge-reranker")
    monkeypatch.setenv("RERANKER_TOP_N", "6")
    monkeypatch.setenv("RERANKER_CANDIDATES", "25")
    monkeypatch.setenv("RERANKER_QUERY_TEMPLATE", "")
    monkeypatch.setenv("RERANKER_INSTRUCTION", "")
    monkeypatch.setenv("RETRIEVAL_TOP_K", "10")
    monkeypatch.setenv("MIN_CONFIDENCE_SCORE", "0.45")
    monkeypatch.setenv("HYBRID_VECTOR_CANDIDATES", "20")
    monkeypatch.setenv("HYBRID_BM25_CANDIDATES", "20")
    monkeypatch.setenv("EMBEDDING_QUERY_PREFIX", "")
    monkeypatch.setenv("EMBEDDING_PASSAGE_PREFIX", "")
    monkeypatch.setenv("CHUNK_MIN_TOKENS", "50")
    monkeypatch.setenv("CHUNK_MAX_TOKENS", "400")
    monkeypatch.setenv("ESCALATION_TICKET_PREFIX", "LEG")
    monkeypatch.setenv("BM25_ENABLED", "false")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.setenv("CHAINLIT_PORT", "8080")
    monkeypatch.setenv("DATABASE_URL", "postgresql://legal:legal@localhost:5434/legal")

    settings = Settings()

    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.llm_model == "llama3"
    assert settings.embedding_model == "nomic-embed-text"
    assert settings.qdrant_vector_dim == 768
    assert settings.qdrant_url == "http://localhost:6333"
    assert settings.redis_url == "redis://:myredissecret@localhost:6379"
    assert settings.reranker_enabled is True
    assert settings.reranker_backend == "llama-cpp"
    assert settings.reranker_url == "http://localhost:8081/v1/rerank"
    assert settings.reranker_model == "bge-reranker"
    assert settings.reranker_top_n == 6
    assert settings.reranker_candidates == 25
    assert settings.retrieval_top_k == 10
    assert settings.min_confidence_score == 0.45
    assert settings.hybrid_vector_candidates == 20
    assert settings.hybrid_bm25_candidates == 20
    assert settings.chunk_min_tokens == 50
    assert settings.chunk_max_tokens == 400
    assert settings.escalation_ticket_prefix == "LEG"
    assert settings.bm25_enabled is False
    assert settings.api_port == 8000
    assert settings.chainlit_port == 8080
    assert settings.database_url == "postgresql://legal:legal@localhost:5434/legal"


def test_config_singleton_returns_same_instance(monkeypatch):
    """get_settings() returns the same cached instance."""
    monkeypatch.setenv("LLM_MODEL", "llama3")
    monkeypatch.setenv("EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def test_settings_default_chat_history_n_turns(monkeypatch):
    """Default chat_history_n_turns is 5."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.chat_history_n_turns == 5


def test_settings_default_chat_history_trim_chars(monkeypatch):
    """Default chat_history_trim_chars is 300."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.chat_history_trim_chars == 300


def test_settings_default_checkpointer_enabled_true(monkeypatch):
    """Default checkpointer_enabled is True."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.checkpointer_enabled is True


def test_settings_default_interrupt_enabled_true(monkeypatch):
    """Default interrupt_enabled is True (resume is now wired)."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.interrupt_enabled is True


def test_settings_default_max_review_iterations(monkeypatch):
    """Default max_review_iterations is 3."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.max_review_iterations == 3


def test_settings_default_checkpoint_ttl_seconds(monkeypatch):
    """Default checkpoint_ttl_seconds is 86400 (24 hours)."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.checkpoint_ttl_seconds == 86400


def test_otel_settings_defaults(monkeypatch):
    for var in ("TRACING_ENABLED", "OTEL_EXPORTER_OTLP_ENDPOINT",
                "OTEL_EXPORTER_OTLP_HEADERS", "OTEL_SERVICE_NAME"):
        monkeypatch.delenv(var, raising=False)
    from config import Settings
    # _env_file=None isolates from a developer's local .env (which may legitimately
    # set OTEL_EXPORTER_OTLP_HEADERS) so this asserts the CODE defaults, not disk state.
    s = Settings(_env_file=None)
    assert s.otel_exporter_otlp_endpoint == "http://localhost:3000/api/public/otel"
    assert s.otel_service_name == "legal-triage"
    assert s.tracing_enabled is True
    assert s.otel_exporter_otlp_headers == ""


def test_legacy_tracing_keys_removed():
    from config import Settings
    s = Settings()
    for attr in ("langfuse_host", "langfuse_public_key", "langfuse_secret_key", "phoenix_host"):
        assert not hasattr(s, attr), f"{attr} should be removed"


def test_chat_budget_fits_context_window():
    """The assembled chat budget plus the answer must fit inside the pinned window.

    This invariant was violated before 2026-08-21: chat_context_max_chars was
    set without reference to ollama_num_ctx, so a full MSA turn overflowed and
    Ollama silently middle-dropped the prompt — which removes exactly the
    playbook/MSA. Asserting it here makes a future mis-tune fail loudly.
    """
    s = get_settings()
    est_input_tokens = s.chat_context_max_chars / s.est_chars_per_token
    assert est_input_tokens + s.ollama_num_predict_chat < s.ollama_num_ctx, (
        f"chat budget {s.chat_context_max_chars} chars "
        f"(~{est_input_tokens:.0f} tok) + {s.ollama_num_predict_chat} answer tokens "
        f"does not fit num_ctx={s.ollama_num_ctx}"
    )


def test_review_headroom_fits_a_real_contract():
    """contract_review has NO input cap, so the window must hold the largest
    real document we have plus its playbook bundle.

    Measured 2026-08-21: the Trinetix Model MSA extracts to 84,859 chars and
    the MSA playbook bundle assembles to 38,587 — 123,446 together. At the old
    num_ctx=32768 with num_predict_review=8192 the headroom was 24,576 tokens
    against a measured 25,270-token input, so MSA reviews ran under-grounded.
    """
    s = get_settings()
    headroom_chars = (s.ollama_num_ctx - s.ollama_num_predict_review) * s.est_chars_per_token
    assert headroom_chars > 123_446, (
        f"review headroom {headroom_chars:.0f} chars cannot hold a real MSA review "
        f"(123,446 chars): num_ctx={s.ollama_num_ctx}, "
        f"num_predict_review={s.ollama_num_predict_review}"
    )
