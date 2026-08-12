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
