# graph/nodes/history_appender.py
"""History appender — pushes the current (user, assistant) pair into chat_history.

Sits between output_formatter and memory_writer. Returns a partial state update;
the chat_history reducer in graph/state.py applies the cap.
"""

import logging

from langfuse.decorators import observe

from config import get_settings
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)


def _trim(s: str, n: int) -> str:
    """Truncate s to n chars and append '[...]' marker if it was longer."""
    return s if len(s) <= n else s[:n] + "[...]"


@observe(name="history_appender")
def history_appender(state: LegalAgentState) -> dict:
    """Append the current turn to chat_history.

    Returns only the partial state update so the reducer in state.py handles
    concatenation and capping.
    """
    settings = get_settings()
    user_msg = {"role": "user", "content": state.get("request", "")}
    assistant_msg = {
        "role": "assistant",
        "content": _trim(state.get("llm_response", ""), settings.chat_history_trim_chars),
    }
    logger.info(
        "[history_appender] appended turn (user_len=%d, assistant_len=%d)",
        len(user_msg["content"]), len(assistant_msg["content"]),
    )
    return {"chat_history": [user_msg, assistant_msg]}
