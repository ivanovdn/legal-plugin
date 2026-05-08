# skills/base.py
"""Base interface for skills. All skills take LegalAgentState and return it."""

from graph.state import LegalAgentState


def stub_skill(name: str):
    """Factory for stub skill functions."""
    def _skill(state: LegalAgentState) -> LegalAgentState:
        state["llm_response"] = f"[{name} stub] No implementation yet."
        return state
    _skill.__name__ = name
    return _skill
