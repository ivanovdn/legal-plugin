from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    fake = SimpleNamespace(preferences_enabled=True, preferences_dir=str(tmp_path))
    monkeypatch.setattr("api.routes.preferences.get_settings", lambda: fake)
    return TestClient(app), fake


def test_put_then_get_round_trip(client):
    c, _ = client
    r = c.put("/api/preferences", json={"markdown": "- pref A"}, headers={"X-User-ID": "atty-1"})
    assert r.status_code == 200 and r.json()["data"]["saved"] is True
    r = c.get("/api/preferences", headers={"X-User-ID": "atty-1"})
    assert r.json()["data"]["markdown"] == "- pref A"


def test_attorneys_isolated(client):
    c, _ = client
    c.put("/api/preferences", json={"markdown": "alpha"}, headers={"X-User-ID": "a"})
    c.put("/api/preferences", json={"markdown": "beta"}, headers={"X-User-ID": "b"})
    assert c.get("/api/preferences", headers={"X-User-ID": "a"}).json()["data"]["markdown"] == "alpha"
    assert c.get("/api/preferences", headers={"X-User-ID": "b"}).json()["data"]["markdown"] == "beta"


def test_get_missing_is_empty(client):
    c, _ = client
    assert c.get("/api/preferences", headers={"X-User-ID": "new"}).json()["data"]["markdown"] == ""


def test_disabled(client):
    c, fake = client
    fake.preferences_enabled = False
    assert c.get("/api/preferences", headers={"X-User-ID": "a"}).json()["data"]["markdown"] == ""
    assert c.put("/api/preferences", json={"markdown": "x"}, headers={"X-User-ID": "a"}).status_code == 403


def test_oversize_413(client):
    c, _ = client
    r = c.put("/api/preferences", json={"markdown": "x" * 20001}, headers={"X-User-ID": "a"})
    assert r.status_code == 413
