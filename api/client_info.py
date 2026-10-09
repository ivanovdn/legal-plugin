# api/client_info.py
"""What sent a request: the Word host, the pane build, the webview.

The pane sends three headers on every request (clients/word/src/requestHeaders.ts);
User-Agent arrives on its own, passed through by Caddy. Read-only facts for the
turn_log row and the trace — never identity, never a key, never a reason to
reject a request: a missing header is just "".
"""
from dataclasses import dataclass

from fastapi import Header

_HEADER_CAP = 64        # platform, version and build are short, and headers are client-controlled
_USER_AGENT_CAP = 300


@dataclass(frozen=True)
class ClientInfo:
    office_platform: str = ""
    office_version: str = ""
    pane_build: str = ""
    user_agent: str = ""


def resolve_client_info(
    x_office_platform: str = Header("", alias="X-Office-Platform"),
    x_office_version: str = Header("", alias="X-Office-Version"),
    x_pane_build: str = Header("", alias="X-Pane-Build"),
    user_agent: str = Header("", alias="User-Agent"),
) -> ClientInfo:
    """The request's client facts, each capped."""
    return ClientInfo(
        office_platform=x_office_platform[:_HEADER_CAP],
        office_version=x_office_version[:_HEADER_CAP],
        pane_build=x_pane_build[:_HEADER_CAP],
        user_agent=user_agent[:_USER_AGENT_CAP],
    )
