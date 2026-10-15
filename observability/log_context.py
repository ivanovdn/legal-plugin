"""The current turn's id on every app log line: `[turn=4b81dc16]`.

Two turns that overlap interleave in the backend log, and without an id the
in-flight `-> ollama` / `<- ollama` pairs cannot be matched across them.
`api/turn_record.py::start_turn` sets the id; api/main.py's handler carries the
filter. A context variable, not a global, so each request's thread or task sees
only its own turn and a line outside any turn reads `[turn=-]`.
"""
from __future__ import annotations

import contextvars
import logging

_turn_id: contextvars.ContextVar[str] = contextvars.ContextVar("log_turn_id", default="")

# The first 8 characters — what the reports and Phoenix show next to a turn.
_SHORT = 8


def set_log_turn(turn_id: str) -> None:
    _turn_id.set(turn_id)


class TurnIdFilter(logging.Filter):
    """Stamps `record.turn` for the format string; never drops a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.turn = _turn_id.get()[:_SHORT] or "-"
        return True
