import observability.otel as otel


def test_normalize_endpoint_appends_v1_traces():
    assert otel._normalize_endpoint("http://langfuse-web:3000/api/public/otel") == \
        "http://langfuse-web:3000/api/public/otel/v1/traces"
    assert otel._normalize_endpoint("http://phoenix:6006") == "http://phoenix:6006/v1/traces"
    assert otel._normalize_endpoint("http://phoenix:6006/v1/traces/") == "http://phoenix:6006/v1/traces"


def test_parse_headers():
    assert otel._parse_headers("Authorization=Basic abc123") == {"Authorization": "Basic abc123"}
    assert otel._parse_headers("a=1,b=2") == {"a": "1", "b": "2"}
    assert otel._parse_headers("") == {}


def test_init_disabled_sets_no_provider(monkeypatch):
    monkeypatch.setattr(otel, "_initialized", False)
    from config import get_settings
    monkeypatch.setenv("TRACING_ENABLED", "false")
    get_settings.cache_clear()
    called = {}
    monkeypatch.setattr(otel.trace, "set_tracer_provider", lambda p: called.setdefault("set", p))

    otel.init_observability()

    assert "set" not in called
    assert otel.is_enabled() is False
    get_settings.cache_clear()


def test_init_enabled_sets_provider(monkeypatch):
    monkeypatch.setattr(otel, "_initialized", False)
    from config import get_settings
    monkeypatch.setenv("TRACING_ENABLED", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://phoenix:6006")
    get_settings.cache_clear()
    called = {}
    monkeypatch.setattr(otel.trace, "set_tracer_provider", lambda p: called.setdefault("set", p))

    otel.init_observability()

    assert "set" in called
    assert otel.is_enabled() is True
    get_settings.cache_clear()
    # init_observability() really calls _instrument_libraries() here (nothing
    # above mocks it) — HTTPXClientInstrumentor().instrument() is global,
    # per-process state, so undo it or it leaks into every later test.
    otel.HTTPXClientInstrumentor().uninstrument()


def test_init_is_best_effort_on_failure(monkeypatch):
    monkeypatch.setattr(otel, "_initialized", False)
    from config import get_settings
    monkeypatch.setenv("TRACING_ENABLED", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://phoenix:6006")
    get_settings.cache_clear()

    def boom(*a, **k):
        raise RuntimeError("exporter blew up")
    monkeypatch.setattr(otel, "OTLPSpanExporter", boom)

    otel.init_observability()   # must NOT raise
    assert otel.is_enabled() is False
    get_settings.cache_clear()


def test_instrument_libraries_is_not_called_when_tracing_is_disabled(monkeypatch):
    """Otherwise the test suite would globally patch httpx for every later test."""
    import observability.otel as otel

    called = []
    monkeypatch.setattr(otel, "_instrument_libraries", lambda: called.append(True))
    monkeypatch.setattr(otel, "_initialized", False)
    monkeypatch.setenv("TRACING_ENABLED", "false")
    from config import get_settings
    get_settings.cache_clear()

    otel.init_observability()
    assert called == []


def test_instrument_libraries_never_raises(monkeypatch):
    """One bad instrumentor must not take down the others, or startup."""
    import observability.otel as otel

    class Boom:
        def instrument(self):
            raise RuntimeError("already instrumented")

    monkeypatch.setattr(otel, "HTTPXClientInstrumentor", lambda: Boom())
    otel._instrument_libraries()   # must not raise


def test_httpx_instrumentation_catches_module_level_post():
    """Settles the one thing the spec flagged as expected-but-unverified: does
    HTTPXClientInstrumentor catch llm_caller's MODULE-LEVEL `httpx.post(...)`
    call? The instrumentor patches HTTPTransport.handle_request, and
    httpx.post() builds a Client() internally that still routes through that
    transport — so it should. A connection that fails to even open still
    produces the span (verified against 127.0.0.1:1, an unbound privileged
    port — the OS refuses the connection immediately), so this needs no live
    server.

    HTTPXClientInstrumentor().instrument() is GLOBAL, per-process state (a
    BaseInstrumentor singleton) — left instrumented, it would silently patch
    httpx for every later test in the suite (e.g. test_turn_outcome.py's
    llm_caller tests). Uninstrument in `finally` so that holds even if the
    assertion below fails.
    """
    import httpx
    from opentelemetry.trace import SpanKind

    from tests.conftest import spans_by_name

    otel._instrument_libraries()
    try:
        try:
            httpx.post("http://127.0.0.1:1", timeout=1.0)
        except httpx.HTTPError:
            pass  # connection refused is expected — the span is recorded regardless

        matches = spans_by_name("POST")
        assert matches, "expected a CLIENT span for the module-level httpx.post call"
        assert matches[0].kind == SpanKind.CLIENT
    finally:
        otel.HTTPXClientInstrumentor().uninstrument()
