# skills/base.py
"""Base utilities for skills. All skills take LegalAgentState and return it."""

from pathlib import Path

from graph.state import LegalAgentState

_CEILING_PREFIX = """STRICT INSTRUCTION: Follow ONLY the playbook below. Do not add analysis, categories, suggestions, or output formats beyond what the playbook specifies. Do not use your training knowledge to supplement — if the playbook doesn't cover something, state "Not covered by current playbook" and stop. You are not a legal consultant. You are an executor of this playbook.

---
PLAYBOOK START
---

"""

_CEILING_SUFFIX = """

---
PLAYBOOK END
---

Remember: produce ONLY what the playbook above instructs. Nothing more."""


def load_skill_prompt(skill_dir: Path) -> str:
    """Load SKILL.md from a skill directory, strip frontmatter, wrap with ceiling constraints."""
    skill_md = skill_dir / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    # Strip YAML frontmatter
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3:].strip()
    return _CEILING_PREFIX + text + _CEILING_SUFFIX


def stub_skill(name: str):
    """Factory for stub skill functions."""
    def _skill(state: LegalAgentState) -> LegalAgentState:
        state["llm_response"] = f"[{name} stub] No implementation yet."
        return state
    _skill.__name__ = name
    return _skill
