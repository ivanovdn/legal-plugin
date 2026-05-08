# graph/nodes/intent_router.py
"""Intent router — classifies task_type from request via LLM."""

import json
import logging

import httpx

from config import get_settings
from graph.state import LegalAgentState

logger = logging.getLogger(__name__)

VALID_TASK_TYPES = {
    "contract_generation", "contract_review", "compliance",
    "research", "drafting",
}

_CLASSIFICATION_PROMPT = """You are a legal task classifier. Given a user request, classify it into exactly one task type.

Valid task types:
- contract_generation: Generate a new contract or agreement
- contract_review: Review, analyze, or extract clauses from an existing contract
- compliance: Check documents against policies, regulations, or jurisdiction rules
- research: Answer legal questions, find precedents, or research legal topics
- drafting: Fill templates to produce NDAs, memos, briefs, or other documents

Respond with JSON only: {{"task_type": "<type>"}}

User request: {request}"""


def intent_router(state: LegalAgentState) -> LegalAgentState:
    """Classify task_type from request. Preserves existing task_type if valid."""
    if state.get("task_type") and state["task_type"] in VALID_TASK_TYPES:
        if not state.get("skill_plan"):
            state["skill_plan"] = [state["task_type"]]
        logger.info("[intent_router] keeping task_type=%s", state["task_type"])
        return state

    settings = get_settings()
    task_type = "research"

    try:
        response = httpx.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.llm_model,
                "messages": [
                    {"role": "user", "content": _CLASSIFICATION_PROMPT.format(request=state["request"])}
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.0},
            },
            timeout=30.0,
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        parsed = json.loads(content)
        classified = parsed.get("task_type", "research")
        if classified in VALID_TASK_TYPES:
            task_type = classified
        logger.info("[intent_router] LLM classified: %s", task_type)
    except Exception as e:
        logger.warning("[intent_router] LLM classification failed: %s — defaulting to research", e)

    state["task_type"] = task_type
    state["skill_plan"] = [task_type]
    return state
