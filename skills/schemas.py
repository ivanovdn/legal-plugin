# skills/schemas.py
"""Output schemas for all skills."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class GeneratedContract(BaseModel):
    """Output of contract_generation skill."""
    doc_type: str
    jurisdiction: str
    full_text: str
    source_contracts: list[str]     # doc_ids used as source
    extracted_patterns: dict        # clause_type -> pattern used
    deviations: list[str]           # patterns absent from history — flagged
    docx_path: str | None = None


class ClauseAnalysis(BaseModel):
    """Single clause analysis for contract review."""
    clause_type: str
    original_text: str
    risk_level: Literal["low", "medium", "high"]
    risk_reason: str
    standard_ref: str | None = None
    suggested_edit: str | None = None


class ContractReviewReport(BaseModel):
    """Output of contract_review skill."""
    contract_name: str
    jurisdiction: str
    clauses: list[ClauseAnalysis]
    summary: str
    missing_clauses: list[str]


class ComplianceCheck(BaseModel):
    """Single compliance check item."""
    rule_id: str
    rule_text: str
    source_chunk: str
    status: Literal["pass", "fail", "partial", "n/a"]
    evidence: str
    remediation: str | None = None


class ComplianceReport(BaseModel):
    """Output of compliance_check skill."""
    jurisdiction: str
    policy_scope: str
    checks: list[ComplianceCheck]
    overall: Literal["pass", "fail", "partial"]
    escalate: bool


class Citation(BaseModel):
    """Citation reference for legal research."""
    chunk_id: str
    doc_title: str
    excerpt: str


class ResearchReport(BaseModel):
    """Output of legal_research skill."""
    question: str
    answer: str
    citations: list[Citation]
    confidence: float
    conflicts: list[str]
    open_gaps: list[str]
    escalate: bool


class DraftResult(BaseModel):
    """Output of drafting skill."""
    doc_type: str
    jurisdiction: str
    full_text: str
    template_ref: str | None = None
    deviations: list[str]
    variables_filled: dict
    docx_path: str | None = None
