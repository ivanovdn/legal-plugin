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
    # Hard ceiling on GENERATED tokens. Unset, Ollama generates until the
    # context window fills — so a degenerate repetition loop runs for ~21k
    # tokens and the turn presents as hung. Observed 2026-08-14 on the VM: the
    # model could not resolve "fill the title" against a title that was already
    # filled, and looped the same self-doubt paragraph inside a JSON rationale
    # until it ran out of room.
    #
    # Chat replies are tiny in practice (13-373 chars observed), so 2048 is
    # already generous; a review legitimately emits ~4.6k tokens of tables, so
    # it gets its own, larger cap. Bounding these turns a degenerate loop into a
    # truncated answer instead of a multi-minute stall.
    ollama_num_predict_chat: int = 2048
    ollama_num_predict_review: int = 8192
    chat_context_max_chars: int = 100000   # assembled chat-context budget; must stay below ollama_num_ctx (in tokens ≈ chars/4) with answer headroom — at 32768 tokens that is ~100k chars plus ~7k tokens answer room.
    chat_conditional_grounding: bool = True   # gate playbook/MSA on _needs_grounding; False = always attach (A/B + future cloud path)
    msa_max_chars: int = 24000             # MSA cap, shared by review + chat paths
    conversation_store_enabled: bool = True   # durable per-(document,attorney) chat store; False = Redis-only history
    conversation_max_messages: int = 20       # messages injected from the durable store (~10 turns); store retains all

    # Attorney preference memory (USER.md) — stage 1 of the self-improving harness
    preferences_enabled: bool = True          # per-attorney USER.md; False = no store/injection
    preferences_dir: str = "data/attorneys"   # USER.md at <preferences_dir>/<attorney_id>/USER.md
    preferences_max_chars: int = 8000         # cap on prefs injected into a prompt (counts to chat budget)

    # Tester feedback capture — written reports + interaction telemetry
    feedback_enabled: bool = True               # False = /api/feedback 403s, /api/events discards
    feedback_snapshot_max_chars: int = 200000   # per-field cap on the replay snapshot, truncation-marked

    # O365 SSO (slice 3) — dormant until sso_enabled; False = trust X-User-ID (today)
    sso_enabled: bool = False
    sso_tenant_id: str = ""      # Azure AD tenant (directory) id
    sso_client_id: str = ""      # app (client) id — expected token audience
    sso_issuer: str = ""         # expected iss; derived from tenant id when empty
    sso_jwks_url: str = ""       # JWKS endpoint; derived from tenant id when empty

    # OpenTelemetry tracing (backend chosen by endpoint: local Langfuse v3 OTLP / VM Phoenix)
    otel_exporter_otlp_endpoint: str = "http://localhost:3000/api/public/otel"
    otel_exporter_otlp_headers: str = ""   # "key=value,key2=value2"; local Langfuse needs Authorization=Basic <b64 public:secret>
    otel_service_name: str = "legal-triage"
    tracing_enabled: bool = True

    # App
    api_port: int = 8000
    chainlit_port: int = 8080
    database_url: str = "postgresql://legal:legal@localhost:5434/legal"
    db_pool_timeout: float = 3.0              # seconds to wait for a pooled connection; psycopg_pool's own default is 30s, long enough that an app-db outage reads as a hang rather than a degrade

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
