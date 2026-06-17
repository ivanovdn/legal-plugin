# graph/nodes/planner.py
"""Planner — breaks multi-skill requests into ordered skill_plan."""

import json
import logging

import httpx

from config import get_settings
from langfuse.decorators import observe, langfuse_context

from graph.state import LegalAgentState
from observability.tracing import ollama_usage

logger = logging.getLogger(__name__)

_PLANNER_PROMPT = """You are a legal task planner. Given a user request that requires multiple legal skills, determine the optimal execution order.

Available skills:
- contract_review: Review and analyze contract clauses
- compliance: Check documents against policies and regulations
- contract_generation: Generate new contracts
- research: Answer legal questions from knowledge base
- drafting: Generate legal documents from templates

The user's request: {request}

Current skill plan: {skill_plan}

Determine which skill should execute FIRST (the most important one for this request).
Respond with JSON: {{"task_type": "<first_skill_to_execute>", "skill_plan": ["<ordered_list>"]}}"""


@observe(name="planner", as_type="generation")
def planner(state: LegalAgentState) -> LegalAgentState:
    """Decompose multi-skill requests. Sets task_type to first skill to execute."""
    skill_plan = state.get("skill_plan", [])

    if len(skill_plan) <= 1:
        logger.info("[planner] single skill, no decomposition needed")
        return state

    settings = get_settings()
    prompt = _PLANNER_PROMPT.format(request=state["request"], skill_plan=skill_plan)

    try:
        response = httpx.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.llm_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.0},
            },
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        content = data["message"]["content"]
        parsed = json.loads(content)

        if "task_type" in parsed:
            state["task_type"] = parsed["task_type"]
        if "skill_plan" in parsed:
            state["skill_plan"] = parsed["skill_plan"]

        langfuse_context.update_current_observation(
            input=prompt,
            output=content,
            model=settings.llm_model,
            usage=ollama_usage(data),
            metadata={"skill_plan": state.get("skill_plan", [])},
        )
        logger.info("[planner] decomposed: task_type=%s, plan=%s", state["task_type"], state["skill_plan"])

    except Exception as e:
        logger.warning("[planner] LLM planning failed: %s — using first skill in plan", e)
        state["task_type"] = skill_plan[0]

    return state
