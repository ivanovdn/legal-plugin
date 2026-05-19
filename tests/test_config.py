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
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-local")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-local")
    monkeypatch.setenv("PHOENIX_HOST", "http://localhost:6006")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.setenv("CHAINLIT_PORT", "8080")
    monkeypatch.setenv("SQLITE_PATH", "data/legal.db")

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
    assert settings.langfuse_host == "http://localhost:3000"
    assert settings.langfuse_public_key == "pk-lf-local"
    assert settings.langfuse_secret_key == "sk-lf-local"
    assert settings.phoenix_host == "http://localhost:6006"
    assert settings.api_port == 8000
    assert settings.chainlit_port == 8080
    assert settings.sqlite_path == "data/legal.db"


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


def test_settings_default_interrupt_enabled_false(monkeypatch):
    """Default interrupt_enabled is False (resume not yet wired)."""
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.interrupt_enabled is False
