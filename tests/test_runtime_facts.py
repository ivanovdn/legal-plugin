"""What a turn ran with — the backend settings and the model on the inference server.

`backend_build` names the code; it does not name the VM's .env (context size,
review thinking, grounding switches), nor which weights `qwen3.6:latest`
resolved to on Spark that day. Both can change mid-pilot without a deploy, and
a comparison across turns is meaningless without them.
"""
from __future__ import annotations

import threading

import httpx
import pytest

import api.runtime_facts as runtime_facts
from api.runtime_facts import (
    NEVER_RECORDED,
    RECORDED,
    ollama_facts,
    reset_ollama_cache,
    settings_snapshot,
)
from config import Settings


@pytest.fixture(autouse=True)
def _fresh_cache():
    reset_ollama_cache()
    yield
    reset_ollama_cache()


def test_every_setting_is_classified_exactly_once():
    """A new setting must be put on one list or the other on purpose — a
    password added later must never reach turn_log by default.

    Mutation: add a field to Settings without touching either list."""
    fields = set(Settings.model_fields)
    assert RECORDED | NEVER_RECORDED == fields, (
        f"unclassified: {sorted(fields - RECORDED - NEVER_RECORDED)}; "
        f"stale: {sorted((RECORDED | NEVER_RECORDED) - fields)}"
    )
    assert not RECORDED & NEVER_RECORDED


def test_credentials_are_never_recorded():
    assert {"database_url", "redis_url", "otel_exporter_otlp_headers"} <= NEVER_RECORDED


def test_the_snapshot_holds_the_recorded_settings_and_nothing_else():
    s = Settings(
        database_url="postgresql://legal:SECRET@db/legal",
        redis_url="redis://:SECRET@redis:6379",
        otel_exporter_otlp_headers="Authorization=Basic SECRET",
        ollama_num_ctx=65536,
        ollama_think_review=False,
    )
    snap = settings_snapshot(s)
    assert set(snap) == RECORDED
    assert snap["ollama_num_ctx"] == 65536
    assert snap["ollama_think_review"] is False
    assert "SECRET" not in repr(snap)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_ollama(monkeypatch, calls: list[str], *, tags=None, version="0.12.3", fail=False,
                 gate: threading.Event | None = None):
    tags = tags if tags is not None else [
        {"name": "qwen3.6:latest", "digest": "aaa111"},
        {"name": "bge-m3:latest", "digest": "bbb222"},
    ]

    def fake_get(url, timeout):
        calls.append(url)
        if gate is not None:
            gate.wait(5)
        if fail:
            raise httpx.ConnectError("refused")
        if url.endswith("/api/tags"):
            return _Resp({"models": tags})
        return _Resp({"version": version})

    monkeypatch.setattr(runtime_facts.httpx, "get", fake_get)


def test_the_digest_is_the_one_behind_the_configured_model_name(monkeypatch):
    _fake_ollama(monkeypatch, [])
    facts = ollama_facts("http://spark:11434", "qwen3.6:latest")
    assert facts == {"model_digest": "aaa111", "ollama_version": "0.12.3"}


def test_a_model_named_without_a_tag_means_latest(monkeypatch):
    _fake_ollama(monkeypatch, [])
    assert ollama_facts("http://spark:11434", "qwen3.6")["model_digest"] == "aaa111"


def test_a_model_the_server_does_not_have_records_no_digest(monkeypatch):
    _fake_ollama(monkeypatch, [])
    assert ollama_facts("http://spark:11434", "llama9:70b")["model_digest"] == ""


def test_an_unreachable_server_records_nothing_and_never_raises(monkeypatch):
    _fake_ollama(monkeypatch, [], fail=True)
    assert ollama_facts("http://spark:11434", "qwen3.6:latest") == {
        "model_digest": "", "ollama_version": "",
    }


def test_the_server_is_asked_at_most_once_a_minute(monkeypatch):
    """Per turn, a cached answer — not two HTTP calls in front of every turn."""
    calls: list[str] = []
    _fake_ollama(monkeypatch, calls)
    clock = [1000.0]
    monkeypatch.setattr(runtime_facts.time, "monotonic", lambda: clock[0])
    ollama_facts("http://spark:11434", "qwen3.6:latest")
    clock[0] += 59
    ollama_facts("http://spark:11434", "qwen3.6:latest")
    assert len(calls) == 2  # tags + version, once
    clock[0] += 2
    ollama_facts("http://spark:11434", "qwen3.6:latest")
    assert len(calls) == 4


def test_a_failure_is_cached_too(monkeypatch):
    """A Spark outage must not add a timeout to every turn."""
    calls: list[str] = []
    _fake_ollama(monkeypatch, calls, fail=True)
    clock = [1000.0]
    monkeypatch.setattr(runtime_facts.time, "monotonic", lambda: clock[0])
    ollama_facts("http://spark:11434", "qwen3.6:latest")
    ollama_facts("http://spark:11434", "qwen3.6:latest")
    assert len(calls) == 1


def test_a_null_version_or_digest_is_recorded_as_empty_never_none(monkeypatch):
    """Both columns are NOT NULL: a None here fails the whole row's write."""
    _fake_ollama(monkeypatch, [], version=None, tags=[{"name": "qwen3.6:latest", "digest": None}])
    assert ollama_facts("http://spark:11434", "qwen3.6:latest") == {
        "model_digest": "", "ollama_version": "",
    }


def test_while_one_request_refreshes_the_others_do_not_wait_or_ask(monkeypatch):
    """When the minute runs out, ONE caller asks the server; everyone arriving
    meanwhile gets the last known answer (or empty) at once instead of each
    queueing up to a timeout of their own."""
    calls: list[str] = []
    gate = threading.Event()
    _fake_ollama(monkeypatch, calls, gate=gate)
    refresher = threading.Thread(target=ollama_facts, args=("http://spark:11434", "qwen3.6:latest"))
    refresher.start()
    while not calls:
        pass
    assert ollama_facts("http://spark:11434", "qwen3.6:latest") == {
        "model_digest": "", "ollama_version": "",
    }
    gate.set()
    refresher.join(5)
    assert calls.count("http://spark:11434/api/tags") == 1
    assert ollama_facts("http://spark:11434", "qwen3.6:latest")["model_digest"] == "aaa111"


def test_the_last_known_answer_is_served_while_it_is_being_refreshed(monkeypatch):
    calls: list[str] = []
    clock = [1000.0]
    monkeypatch.setattr(runtime_facts.time, "monotonic", lambda: clock[0])
    _fake_ollama(monkeypatch, calls)
    ollama_facts("http://spark:11434", "qwen3.6:latest")
    clock[0] += 61
    gate = threading.Event()
    _fake_ollama(monkeypatch, calls, gate=gate, version="0.13.0")
    refresher = threading.Thread(target=ollama_facts, args=("http://spark:11434", "qwen3.6:latest"))
    refresher.start()
    while len(calls) < 3:
        pass
    assert ollama_facts("http://spark:11434", "qwen3.6:latest")["ollama_version"] == "0.12.3"
    gate.set()
    refresher.join(5)
    assert ollama_facts("http://spark:11434", "qwen3.6:latest")["ollama_version"] == "0.13.0"


def test_credentials_inside_a_recorded_url_are_dropped():
    """No URL carries one today, but one pasted into .env must not reach the table."""
    snap = settings_snapshot(Settings(
        qdrant_url="http://user:pw@qdrant:6333/?api_key=SECRET#frag",
        ollama_base_url="http://172.20.0.22:11434",
    ))
    assert snap["qdrant_url"] == "http://qdrant:6333/"
    assert snap["ollama_base_url"] == "http://172.20.0.22:11434"
    assert "SECRET" not in repr(snap) and "pw" not in repr(snap)
