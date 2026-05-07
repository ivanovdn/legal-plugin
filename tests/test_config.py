# tests/test_config.py
import os
import pytest


def test_config_loads_from_env(monkeypatch):
    """Config loads all fields from environment variables."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.setenv("LLM_MODEL", "llama3")
    monkeypatch.setenv("EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("RERANKER_ENABLED", "true")
    monkeypatch.setenv("RERANKER_BACKEND", "llama-cpp")
    monkeypatch.setenv("RERANKER_URL", "http://localhost:8081/v1/rerank")
    monkeypatch.setenv("RERANKER_MODEL", "bge-reranker")
    monkeypatch.setenv("RERANKER_TOP_N", "6")
    monkeypatch.setenv("RERANKER_CANDIDATES", "25")
    monkeypatch.setenv("BM25_ENABLED", "false")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("PHOENIX_HOST", "http://localhost:6006")
    monkeypatch.setenv("API_PORT", "8000")
    monkeypatch.setenv("CHAINLIT_PORT", "8080")
    monkeypatch.setenv("SQLITE_PATH", "data/legal.db")

    from config import Settings
    settings = Settings()

    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.llm_model == "llama3"
    assert settings.embedding_model == "nomic-embed-text"
    assert settings.qdrant_vector_dim == 768
    assert settings.qdrant_url == "http://localhost:6333"
    assert settings.redis_url == "redis://localhost:6379"
    assert settings.reranker_enabled is True
    assert settings.reranker_backend == "llama-cpp"
    assert settings.reranker_url == "http://localhost:8081/v1/rerank"
    assert settings.reranker_model == "bge-reranker"
    assert settings.reranker_top_n == 6
    assert settings.reranker_candidates == 25
    assert settings.bm25_enabled is False
    assert settings.langfuse_host == "http://localhost:3000"
    assert settings.langfuse_public_key == "pk-test"
    assert settings.langfuse_secret_key == "sk-test"
    assert settings.phoenix_host == "http://localhost:6006"
    assert settings.api_port == 8000
    assert settings.chainlit_port == 8080
    assert settings.sqlite_path == "data/legal.db"


def test_config_singleton_returns_same_instance(monkeypatch):
    """get_settings() returns the same cached instance."""
    monkeypatch.setenv("LLM_MODEL", "llama3")
    monkeypatch.setenv("EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")

    from config import get_settings
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
