# config.py
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM
    ollama_base_url: str = "http://localhost:11434"
    llm_model: str = ""
    embedding_model: str = ""
    qdrant_vector_dim: int = 0

    # Qdrant
    qdrant_url: str = "http://localhost:6333"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Reranker
    reranker_enabled: bool = True
    reranker_backend: str = ""
    reranker_url: str = ""
    reranker_model: str = ""
    reranker_top_n: int = 6
    reranker_candidates: int = 25

    # BM25
    bm25_enabled: bool = False

    # Langfuse
    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # Phoenix
    phoenix_host: str = "http://localhost:6006"

    # App
    api_port: int = 8000
    chainlit_port: int = 8080
    sqlite_path: str = "data/legal.db"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
