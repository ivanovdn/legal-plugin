"""What sent a request: the pane's headers, the webview, the backend build."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api.client_info import ClientInfo, resolve_client_info
from config import Settings

_app = FastAPI()


@_app.get("/probe")
def _probe(client: ClientInfo = Depends(resolve_client_info)) -> dict:
    return asdict(client)


def test_the_pane_headers_and_the_user_agent_are_read():
    got = TestClient(_app).get("/probe", headers={
        "X-Office-Platform": "Mac", "X-Office-Version": "16.89",
        "X-Pane-Build": "abc1234", "User-Agent": "Mozilla/5.0 (Macintosh)",
    }).json()
    assert got == {"office_platform": "Mac", "office_version": "16.89",
                   "pane_build": "abc1234", "user_agent": "Mozilla/5.0 (Macintosh)"}


def test_a_pane_from_before_this_change_sends_none_and_that_is_fine():
    """Review Focus #1: every pane opened before the deploy sends no X-* headers."""
    got = TestClient(_app).get("/probe", headers={"User-Agent": ""}).json()
    assert got == {"office_platform": "", "office_version": "", "pane_build": "",
                   "user_agent": ""}


def test_oversize_headers_are_capped():
    got = TestClient(_app).get("/probe", headers={
        "X-Office-Platform": "P" * 500, "X-Pane-Build": "b" * 500, "User-Agent": "U" * 1000,
    }).json()
    assert len(got["office_platform"]) == 64 and len(got["pane_build"]) == 64
    assert len(got["user_agent"]) == 300


def test_the_backend_build_defaults_to_dev_and_reads_BUILD_SHA(monkeypatch):
    monkeypatch.delenv("BUILD_SHA", raising=False)
    assert Settings().build_sha == "dev"
    monkeypatch.setenv("BUILD_SHA", "abc1234")
    assert Settings().build_sha == "abc1234"
