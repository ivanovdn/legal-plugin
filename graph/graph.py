# graph/graph.py
"""LangGraph supervisor graph — wires all nodes, skills, and routing."""

from langgraph.graph import END, StateGraph

from graph.state import LegalAgentState

# Shared nodes
from graph.nodes.intake import intake
from graph.nodes.intent_router import intent_router
from graph.nodes.planner import planner
from graph.nodes.skill_dispatcher import skill_dispatcher, route_to_skill
from graph.nodes.rag_retriever import rag_retriever
from graph.nodes.llm_caller import llm_caller
from graph.nodes.risk_assessor import risk_assessor, route_risk
from graph.nodes.human_review import human_review
from graph.nodes.output_formatter import output_formatter
from graph.nodes.memory_writer import memory_writer

# Skills
from skills.contract_generation import contract_generation
from skills.contract_review import contract_review
from skills.compliance_check import compliance_check
from skills.legal_research import legal_research
from skills.drafting import drafting


def route_intent(state: LegalAgentState) -> str:
    """Route to planner (multi-skill) or skill_dispatcher (single skill)."""
    if len(state.get("skill_plan", [])) > 1:
        return "planner"
    return "skill_dispatcher"


def build_graph(checkpointer=None) -> StateGraph:
    """Build and compile the supervisor graph. Returns compiled graph."""
    graph = StateGraph(LegalAgentState)

    # Add shared nodes
    graph.add_node("intake", intake)
    graph.add_node("intent_router", intent_router)
    graph.add_node("planner", planner)
    graph.add_node("skill_dispatcher", skill_dispatcher)
    graph.add_node("rag_retriever", rag_retriever)
    graph.add_node("llm_caller", llm_caller)
    graph.add_node("risk_assessor", risk_assessor)
    graph.add_node("human_review", human_review)
    graph.add_node("output_formatter", output_formatter)
    graph.add_node("memory_writer", memory_writer)

    # Add skill nodes
    graph.add_node("contract_generation", contract_generation)
    graph.add_node("contract_review", contract_review)
    graph.add_node("compliance_check", compliance_check)
    graph.add_node("legal_research", legal_research)
    graph.add_node("drafting", drafting)

    # Entry point
    graph.set_entry_point("intake")

    # Edges: intake -> intent_router
    graph.add_edge("intake", "intent_router")

    # Conditional: intent_router -> planner OR skill_dispatcher
    graph.add_conditional_edges("intent_router", route_intent, {
        "planner": "planner",
        "skill_dispatcher": "skill_dispatcher",
    })

    # planner -> skill_dispatcher
    graph.add_edge("planner", "skill_dispatcher")

    # Conditional: skill_dispatcher -> one of the 5 skills
    graph.add_conditional_edges("skill_dispatcher", route_to_skill, {
        "contract_generation": "contract_generation",
        "contract_review": "contract_review",
        "compliance_check": "compliance_check",
        "legal_research": "legal_research",
        "drafting": "drafting",
    })

    # All skills -> rag_retriever
    for skill in ["contract_generation", "contract_review", "compliance_check", "legal_research", "drafting"]:
        graph.add_edge(skill, "rag_retriever")

    # rag_retriever -> llm_caller -> risk_assessor
    graph.add_edge("rag_retriever", "llm_caller")
    graph.add_edge("llm_caller", "risk_assessor")

    # Conditional: risk_assessor -> human_review OR output_formatter
    graph.add_conditional_edges("risk_assessor", route_risk, {
        "human_review": "human_review",
        "output_formatter": "output_formatter",
    })

    # human_review -> output_formatter
    graph.add_edge("human_review", "output_formatter")

    # output_formatter -> memory_writer -> END
    graph.add_edge("output_formatter", "memory_writer")
    graph.add_edge("memory_writer", END)

    return graph.compile(checkpointer=checkpointer)
