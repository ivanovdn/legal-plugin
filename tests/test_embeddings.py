# tests/test_embeddings.py
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

import rag.embeddings as emb
from config import get_settings

_OVER_LENGTH = '{"error":"the input length exceeds the context length"}'
_URL = "http://ollama.test/api/embed"


def test_embed_texts_calls_ollama(monkeypatch):
    """embed_texts() sends correct payload to Ollama /api/embed."""
    monkeypatch.setenv("EMBEDDING_MODEL", "embeddinggemma:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    import importlib
    import rag.embeddings as mod
    importlib.reload(mod)

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "embeddings": [[0.1] * 768, [0.2] * 768]
    }

    with patch("rag.embeddings.httpx.post", return_value=fake_response) as mock_post:
        result = mod.embed_texts(["hello", "world"])

    assert len(result) == 2
    assert len(result[0]) == 768
    call_args = mock_post.call_args
    body = call_args[1]["json"]
    assert body["model"] == "embeddinggemma:latest"


def test_embed_query_calls_ollama(monkeypatch):
    """embed_query() returns a single vector."""
    monkeypatch.setenv("EMBEDDING_MODEL", "embeddinggemma:latest")
    monkeypatch.setenv("QDRANT_VECTOR_DIM", "768")
    get_settings.cache_clear()

    import importlib
    import rag.embeddings as mod
    importlib.reload(mod)

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.json.return_value = {
        "embeddings": [[0.5] * 768]
    }

    with patch("rag.embeddings.httpx.post", return_value=fake_response):
        result = mod.embed_query("test query")

    assert len(result) == 768


# --- truncate:false (D3) ------------------------------------------------------
#
# These drive rag.embeddings with httpx.post faked, and read the module as
# `emb.X` at call time: the two tests above importlib.reload() the module, which
# rebinds every class in it, so a `from rag.embeddings import
# EmbeddingInputTooLong` made at import time would name a class the code no
# longer raises.


def _response(status: int, body: dict | None = None, text: str | None = None):
    """A real httpx.Response, so raise_for_status / .text / .json() are the real ones."""
    request = httpx.Request("POST", _URL)
    if text is not None:
        return httpx.Response(status, text=text, request=request)
    return httpx.Response(status, json=body or {}, request=request)


def _over_length():
    return _response(400, text=_OVER_LENGTH)


def _ok(n: int = 1):
    return _response(200, {"embeddings": [[0.1, 0.2]] * n})


@pytest.fixture
def settings(monkeypatch):
    s = SimpleNamespace(
        ollama_base_url="http://ollama.test", embedding_model="embed-model",
        embedding_passage_prefix="", embedding_query_prefix="",
    )
    monkeypatch.setattr(emb, "get_settings", lambda: s)
    return s


def _fake_post(monkeypatch, handler):
    """Patch httpx.post on the module; return the list of request bodies it saw."""
    bodies: list[dict] = []

    def post(url, json=None, timeout=None, **_):
        bodies.append(json)
        return handler(json)

    monkeypatch.setattr(emb.httpx, "post", post)
    return bodies


def test_every_request_sends_truncate_false(settings, monkeypatch):
    bodies = _fake_post(monkeypatch, lambda body: _response(
        200, {"embeddings": [[0.1]] * len(body["input"]), "prompt_eval_count": 3}))

    emb.embed_texts(["a"])
    emb.embed_query("q")
    emb.prompt_token_count("t")

    assert len(bodies) == 3
    assert all(b["truncate"] is False for b in bodies)


def test_ingest_over_length_raises_naming_the_item(settings, monkeypatch):
    def handler(body):
        texts = body["input"]
        if len(texts) > 1 or len(texts[0]) > 8000:
            return _over_length()
        return _ok()

    bodies = _fake_post(monkeypatch, handler)

    with pytest.raises(emb.EmbeddingInputTooLong) as e:
        emb.embed_texts(["ok", "x" * 9000])

    assert e.value.index == 1 and e.value.chars == 9000
    assert "#1" in str(e.value) and "9000" in str(e.value)
    assert "nothing was cut" in str(e.value)
    # The error never came from a retry that allowed truncation.
    assert all(b["truncate"] is False for b in bodies)


def test_ingest_over_length_reports_chars_without_the_passage_prefix(settings, monkeypatch):
    settings.embedding_passage_prefix = "passage: "
    _fake_post(monkeypatch, lambda body: _over_length())

    with pytest.raises(emb.EmbeddingInputTooLong) as e:
        emb.embed_texts(["x" * 9000])

    assert e.value.index == 0 and e.value.chars == 9000


def test_ingest_batch_that_fits_is_one_request(settings, monkeypatch):
    bodies = _fake_post(monkeypatch, lambda body: _ok(len(body["input"])))

    assert emb.embed_texts(["a", "b", "c"]) == [[0.1, 0.2]] * 3
    assert len(bodies) == 1


def test_query_over_length_retries_once_truncated_and_records(settings, monkeypatch):
    responses = iter([_over_length(), _ok()])
    bodies = _fake_post(monkeypatch, lambda body: next(responses))
    recorded = MagicMock()
    monkeypatch.setattr(emb, "record_degradation", recorded)

    vector = emb.embed_query("q" * 9000)

    assert vector == [0.1, 0.2]
    assert [b["truncate"] for b in bodies] == [False, True]
    recorded.assert_called_once()
    assert recorded.call_args.args == (emb.EMBEDDING_QUERY_TRUNCATED,)
    assert recorded.call_args.kwargs["announced"] is False
    assert "9000" in recorded.call_args.kwargs["detail"]


def test_query_that_fits_records_nothing(settings, monkeypatch):
    _fake_post(monkeypatch, lambda body: _ok())
    recorded = MagicMock()
    monkeypatch.setattr(emb, "record_degradation", recorded)

    emb.embed_query("short")

    recorded.assert_not_called()


def test_other_http_errors_still_raise(settings, monkeypatch):
    bodies = _fake_post(monkeypatch, lambda body: _response(500, text="boom"))
    recorded = MagicMock()
    monkeypatch.setattr(emb, "record_degradation", recorded)

    with pytest.raises(httpx.HTTPStatusError):
        emb.embed_query("q")
    with pytest.raises(httpx.HTTPStatusError):
        emb.embed_texts(["a"])

    assert len(bodies) == 2          # no retry on either path
    recorded.assert_not_called()


@pytest.mark.parametrize("status, text", [
    (400, '{"error":"model not found"}'),   # a 400 that is not about length
    (500, _OVER_LENGTH),                    # the length wording, but not a 400
])
def test_only_a_400_about_length_counts_as_over_length(settings, monkeypatch, status, text):
    bodies = _fake_post(monkeypatch, lambda body: _response(status, text=text))
    recorded = MagicMock()
    monkeypatch.setattr(emb, "record_degradation", recorded)

    with pytest.raises(httpx.HTTPStatusError):
        emb.embed_query("q")
    with pytest.raises(httpx.HTTPStatusError):
        emb.embed_texts(["a"])

    assert len(bodies) == 2          # one request each: no retry, no one-at-a-time pass
    recorded.assert_not_called()


def test_prompt_token_count(settings, monkeypatch):
    _fake_post(monkeypatch, lambda body: _response(
        200, {"embeddings": [[0.1]], "prompt_eval_count": 4}))
    assert emb.prompt_token_count("some text") == 4


def test_prompt_token_count_counts_the_passage_prefix(settings, monkeypatch):
    # The chunker's budget is in the tokens ingest embeds, and ingest embeds with the passage prefix.
    settings.embedding_passage_prefix = "passage: "
    bodies = _fake_post(monkeypatch, lambda body: _response(
        200, {"embeddings": [[0.1]], "prompt_eval_count": 6}))
    emb.prompt_token_count("some text")
    assert bodies[0]["input"] == ["passage: some text"]


def test_prompt_token_count_is_none_over_the_window(settings, monkeypatch):
    _fake_post(monkeypatch, lambda body: _over_length())
    assert emb.prompt_token_count("x" * 9000) is None


def test_prompt_token_count_raises_on_other_errors(settings, monkeypatch):
    _fake_post(monkeypatch, lambda body: _response(500, text="boom"))
    with pytest.raises(httpx.HTTPStatusError):
        emb.prompt_token_count("t")
