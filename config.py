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
    redis_url: str = "redis://:myredissecret@localhost:6379"

    # Reranker
    reranker_enabled: bool = True
    reranker_backend: str = ""
    reranker_url: str = ""
    reranker_model: str = ""
    reranker_top_n: int = 6
    reranker_candidates: int = 25
    reranker_query_template: str = ""
    reranker_instruction: str = ""

    # Retrieval
    retrieval_top_k: int = 10
    min_confidence_score: float = 0.45

    # Hybrid search
    hybrid_vector_candidates: int = 20
    hybrid_bm25_candidates: int = 20

    # Embedding
    embedding_query_prefix: str = ""
    embedding_passage_prefix: str = ""

    # Chunking
    chunk_min_tokens: int = 50
    chunk_max_tokens: int = 400

    # Escalation
    escalation_ticket_prefix: str = "LEG"

    # BM25
    bm25_enabled: bool = False

    # Memory / checkpointer
    checkpointer_enabled: bool = True
    interrupt_enabled: bool = True
    chat_history_n_turns: int = 5
    chat_history_trim_chars: int = 300
    max_review_iterations: int = 3
    checkpoint_ttl_seconds: int = 86400
    ollama_num_ctx: int = 32768            # context window for grounded LLM calls (playbook+MSA+doc+answer); qwen3.6 supports 262k. Raise/lower per hardware (bigger = more KV-cache RAM).
    chat_context_max_chars: int = 100000   # assembled chat-context budget; must stay below ollama_num_ctx (in tokens ≈ chars/4) with answer headroom — at 32768 tokens that is ~100k chars plus ~7k tokens answer room.
    chat_conditional_grounding: bool = True   # gate playbook/MSA on _needs_grounding; False = always attach (A/B + future cloud path)
    msa_max_chars: int = 24000             # MSA cap, shared by review + chat paths
    conversation_store_enabled: bool = True   # durable per-(document,attorney) chat store; False = Redis-only history
    conversation_max_messages: int = 20       # messages injected from the durable store (~10 turns); store retains all

    # Langfuse
    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str = "pk-lf-local"
    langfuse_secret_key: str = "sk-lf-local"

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
