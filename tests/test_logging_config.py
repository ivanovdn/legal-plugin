"""Application log records must actually reach the log in production.

Uvicorn configures only its own `uvicorn*` loggers and leaves the ROOT logger at
WARNING with no handler. So once the app ran under uvicorn, every `logger.info`
in api/, graph/, memory/ and skills/ was discarded — only ERROR escaped, via
`logging.lastResort`. That silently disabled the in-flight turn logging built so
a slow LLM could be told apart from a wedged one, which is the one thing that
made a hang diagnosable on the VM at all.

Confirmed on SRV-AGENT-01 (2026-08-19): a backend restart inside the log window
produced no "Legal plugin API started" line, and two LLM turns produced no
`[llm_caller] -> ollama`.

These tests run in a SUBPROCESS on purpose, and that is the whole point of the
file. pytest's logging plugin installs its own root handler, and
`caplog.at_level()` forces the level on top of that — so every caplog-based
assertion in tests/test_inflight_logging.py passes whether or not the
application configures logging at all. That is exactly how this shipped broken:
a green suite asserting on a handler the test harness supplied. Only a fresh
interpreter reproduces what uvicorn sees.
"""
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Emitted from a real application logger, not the root one — the bug was about
# records PROPAGATING from app modules to a root that could not handle them.
_EMIT = (
    "import api.main, logging\n"
    "log = logging.getLogger('graph.nodes.llm_caller')\n"
    "log.info('MARKER-INFO')\n"
    "log.debug('MARKER-DEBUG')\n"
)


def _run(snippet: str, **env_over) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True, text=True, cwd=REPO_ROOT,
        env={**os.environ, **env_over}, timeout=180,
    )


def test_importing_the_app_makes_info_reach_the_log():
    proc = _run(_EMIT)
    assert proc.returncode == 0, proc.stderr
    assert "MARKER-INFO" in proc.stderr, (
        "an app-level INFO record never reached a handler — this is the "
        "production silence, reproduced"
    )


def test_debug_stays_off_by_default():
    """Pins the default at INFO rather than 'anything that passes the test above'.

    DEBUG on by default would flood the VM's logs with httpx/langchain internals
    and make the in-flight lines harder to find, not easier.
    """
    proc = _run(_EMIT)
    assert "MARKER-DEBUG" not in proc.stderr


def test_log_level_is_settable():
    """LOG_LEVEL has to be a real settings field, not a bare os.environ read.

    pydantic-settings forbids extra keys in .env, so an env var the Settings
    model does not declare crashes the backend on startup with
    `ValidationError: Extra inputs are not permitted` (see docs/deploy-vm.md).
    Reading it off os.environ would work in the shell and break the one place
    an operator would actually put it — and would still satisfy the subprocess
    assertion below, so the declared field is pinned directly.
    """
    from config import Settings
    assert "log_level" in Settings.model_fields, "LOG_LEVEL is not a declared setting"

    proc = _run(_EMIT, LOG_LEVEL="DEBUG")
    assert proc.returncode == 0, proc.stderr
    assert "MARKER-DEBUG" in proc.stderr


def test_uvicorn_records_are_not_duplicated():
    """Under uvicorn for real: our handler must not double-log uvicorn's own.

    uvicorn's `uvicorn` and `uvicorn.access` loggers set propagate=False, so
    their records stop at uvicorn's handler and never reach the root handler
    installed here. Configuring logging with force=True, or attaching a second
    handler to the uvicorn loggers, would print every access line twice — so
    this pins the interaction, not just our own records.

    Order mirrors the server: uvicorn configures logging first, then imports
    the app.
    """
    proc = _run(
        "from uvicorn.config import Config\n"
        "Config('api.main:app').configure_logging()\n"
        "import api.main, logging\n"
        "logging.getLogger('uvicorn.error').info('MARKER-UVICORN')\n"
        "logging.getLogger('api.main').info('MARKER-APP')\n"
    )
    assert proc.returncode == 0, proc.stderr
    combined = proc.stderr + proc.stdout
    assert "MARKER-APP" in combined, "app logging lost when uvicorn configures first"
    assert combined.count("MARKER-UVICORN") == 1, "uvicorn's own records are duplicated"
