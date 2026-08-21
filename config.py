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
    # Pinned to the window the inference server actually loads. This must be
    # EQUAL to the server's, not merely large enough: Ollama reloads the model
    # whenever a request's num_ctx differs from the resident one, in EITHER
    # direction. Measured 2026-08-21 on Spark — requesting 32768 against a
    # resident 131072 forced a 4.7s reload and dropped it to 27.07GB — so a
    # "conservatively smaller" value is not safe, it thrashes a 24GB model in
    # and out and contends with every other consumer of that box.
    #
    # 131072 is what Spark (172.20.0.22) serves. KV costs only ~49.5 MB per 1k
    # tokens because qwen3.6 is a hybrid SSM/attention MoE
    # (full_attention_interval=4 over block_count=40 => ~10 attention layers;
    # the other 30 are SSM layers holding constant-size state), so 131072 costs
    # ~6.34GB and even the full 262144 window costs ~12.7GB. Memory is not the
    # constraint here; prefill latency is (see chat_context_max_chars).
    #
    # COUPLING: this tracks the server's OLLAMA_CONTEXT_LENGTH. If the service
    # is retuned we get reload thrash — a visible 4.7s penalty, not silent
    # truncation, which is the right failure mode. Verify after deploy:
    #   curl http://<ollama-host>:11434/api/ps   -> ctx must equal this value.
    ollama_num_ctx: int = 131072
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
    # Derived from a 30s turn ceiling, not chosen. Measured on Spark 2026-08-21
    # (qwen3.6: prefill 1,397 tok/s, generation 51.4 tok/s):
    #     answer   400 tok / 51.4 tok/s        =   7.8s
    #     prefill  (30 - 7.8) * 1,397 tok/s    =  31,013 tokens
    #     chars    31,013 * 4.89               = ~151,653  -> 150,000
    #
    # At 150k the full Trinetix MSA (84,859) + MSA playbook bundle (38,587) +
    # a prior-review block (~5,000) all fit, leaving ~21,500 for history.
    #
    # NOTE the constraint has moved: 150k chars is ~30.7k tokens against a
    # 131,072 window — 4x headroom. The WINDOW is no longer binding, PREFILL
    # LATENCY is. That is why this is not simply set to the window, and why
    # history compaction is about bounding prefill rather than about fitting.
    chat_context_max_chars: int = 150000
    # Measured on real legal text, not a rule of thumb: the Trinetix MSA plus
    # its playbook bundle is 123,612 chars = 25,270 real prompt tokens (the
    # assembled prompt sent to Ollama, including --- ATTACHED DOCUMENT --- /
    # --- END --- / User request: wrappers); the raw component sum is 123,446
    # (84,859 doc + 38,587 playbook, with no wrapper — the figure in
    # test_review_headroom_fits_a_real_contract). The familiar chars/4 estimate
    # overstates token counts by ~22%, which is why every budget comment that
    # used it was wrong. Used for the budget invariants in tests/test_config.py
    # and the review-path overflow guard in graph/nodes/llm_caller.py.
    est_chars_per_token: float = 4.89
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
    log_level: str = "INFO"                   # root log level; a real settings field because pydantic-settings forbids extra .env keys, so a bare os.environ read would crash the backend the moment an operator set it in .env

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
