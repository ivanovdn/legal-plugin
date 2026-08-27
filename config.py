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

    # History compaction — condense earlier conversation into validated verbatim
    # quotes so the DOCUMENT stops being what gets cut. History is compressible;
    # the contract is not. See
    # docs/superpowers/specs/2026-08-25-context-compaction-design.md.
    compaction_enabled: bool = True
    # The FLOOR that is never condensed, not a window. It was 6 — a message COUNT
    # guarding a size budget, which is the wrong unit: with exactly 6 messages stored
    # nothing was condensable even while the document was being truncated. 2 keeps the
    # last turn verbatim so a follow-up ("make that change", "use the name we agreed")
    # still resolves, and everything older becomes available.
    compaction_keep_recent_messages: int = 2
    # Floor on a segment's size. When a segment is shrunk to free a target number of
    # chars it can be trimmed a long way, but a two-line summary of twenty messages is
    # not worth the latency — below this, accept that compaction cannot reach the target
    # and say so instead of pretending.
    compaction_min_quotes: int = 4
    compaction_warn_pct: int = 90              # budget share at which the Condense action appears
    # 24 quotes holds one segment to roughly 400-500 tokens, and 3 injected
    # segments to ~1,500 tokens ~= 7,300 chars. Both bounds are load-bearing:
    # on the grounded-MSA case history's ENTIRE allowance is 18,554 chars =
    # 3,794 tokens, so unbounded segments would grow past the space history had
    # in the first place and start pushing the document toward truncation —
    # exactly the outcome compaction exists to prevent. Older segments stay in
    # the store, auditable, simply outside the injection window (the same
    # pattern conversation_max_messages already uses).
    compaction_max_quotes: int = 24
    compaction_max_injected_segments: int = 3
    # Fire compaction without a click. Default ON deliberately: the flag exists so a
    # pilot can switch the behaviour off after seeing it, not so someone has to
    # switch it on to see it at all.
    compaction_auto: bool = True
    # The ONE floor for firing UNASKED, and it is derived from the SEGMENT FORMAT,
    # not from the budget and not from an incident. A segment costs a 261-char
    # header plus ~20 per quote line, and the trim loop stops at
    # compaction_min_quotes=4, so the smallest segment we can emit is ~940 chars.
    # Below roughly that, condensing makes history BIGGER and the net-benefit guard
    # declines — measured, 1,293 chars in produced 1,825 out. 4,000 clears every
    # measured decline by 3x while staying far below the point where the document
    # starts being cut.
    #
    # It replaced two floors that were both DAMAGE-FIRST — they could not arm until
    # after the contract had already been truncated. Measured locally 2026-08-27,
    # grounded MSA turn at a 90,000 budget: playbook 30,412 + MSA 24,676 + system
    # 7,212 + document 16,008 = 78,308 of fixed content, leaving history an
    # allowance of 11,692. The old char floor was 20,000 — nearly TWICE what
    # history can ever hold — so it could not arm until ~8,300 characters of
    # contract had already been dropped. The old message floor (6) was the same
    # failure in the other unit: the turn that truncated had 4 compressible
    # messages and 12,485 chars, and auto stayed silent.
    #
    # The lesson, third occurrence: an absolute floor is only safe when its unit is
    # a property of the THING IT BOUNDS. Segment overhead is a property of the
    # format and holds at any budget. "20,000 characters" and "6 messages" were
    # properties of one afternoon's incident, and both went stale the moment the
    # grounding grew. If a floor's justification cites a measurement of the
    # environment rather than of the mechanism, it will go stale.
    compaction_auto_min_chars: int = 4000

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
