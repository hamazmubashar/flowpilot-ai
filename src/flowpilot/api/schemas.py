"""Response models (request models live in flowpilot.domain.models)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from flowpilot.domain.models import (
    BudgetBand,
    CompanySize,
    Industry,
    LeadSource,
    LeadStatus,
    Route,
    ScoreResult,
    ServiceLine,
    Tier,
    Timeline,
)


class LeadOut(BaseModel):
    id: int
    company_name: str
    contact_name: str
    contact_email: str
    company_size: CompanySize
    industry: Industry
    service_interest: ServiceLine
    budget_band: BudgetBand
    timeline: Timeline
    source: LeadSource
    message: str
    status: LeadStatus
    created_at: str
    updated_at: str


class ScoreSummary(BaseModel):
    total: int
    tier: Tier
    route: Route


class LeadSummary(LeadOut):
    score: ScoreSummary | None


class LeadDetail(LeadOut):
    score: ScoreResult | None


class LeadSubmission(BaseModel):
    lead: LeadDetail
    duplicate: bool
    replayed: bool


class LeadPage(BaseModel):
    items: list[LeadSummary]
    total: int
    limit: int
    offset: int


class AuditEntry(BaseModel):
    id: int
    occurred_at: str
    actor: str
    action: str
    entity_type: str
    entity_id: str
    before: Any
    after: Any
    request_id: str | None


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail


class Health(BaseModel):
    status: str
    database: str
