"""Domain enums and validated input models."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from flowpilot.domain.normalize import (
    company_key,
    email_domain,
    is_synthetic_domain,
    normalize_email,
)


class CompanySize(StrEnum):
    S_1_10 = "1-10"
    S_11_50 = "11-50"
    S_51_200 = "51-200"
    S_201_1000 = "201-1000"
    S_1000_PLUS = "1000+"


class Industry(StrEnum):
    SAAS = "saas"
    FINTECH = "fintech"
    HEALTHCARE = "healthcare"
    RETAIL_ECOMMERCE = "retail_ecommerce"
    MANUFACTURING = "manufacturing"
    LOGISTICS = "logistics"
    PROFESSIONAL_SERVICES = "professional_services"
    NONPROFIT = "nonprofit"
    OTHER = "other"


class ServiceLine(StrEnum):
    PROCESS_AUTOMATION = "process_automation"
    DATA_ANALYTICS = "data_analytics"
    AI_STRATEGY = "ai_strategy"
    SYSTEMS_INTEGRATION = "systems_integration"
    DIGITAL_TRANSFORMATION = "digital_transformation"
    OTHER = "other"


class BudgetBand(StrEnum):
    UNDER_10K = "under_10k"
    K10_25 = "10k_25k"
    K25_50 = "25k_50k"
    K50_100 = "50k_100k"
    OVER_100K = "over_100k"
    UNDISCLOSED = "undisclosed"


class Timeline(StrEnum):
    IMMEDIATE = "immediate"
    M1_3 = "1_3_months"
    M3_6 = "3_6_months"
    EXPLORING = "exploring"


class LeadSource(StrEnum):
    WEB_FORM = "web_form"
    REFERRAL = "referral"
    EVENT = "event"
    PARTNER = "partner"


class LeadStatus(StrEnum):
    NEW = "new"
    SCORED = "scored"
    DRAFTED = "drafted"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SENT = "sent"
    CLOSED = "closed"


class Tier(StrEnum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


class Route(StrEnum):
    PARTNER_REVIEW = "partner_review"
    SALES_QUEUE = "sales_queue"
    NURTURE = "nurture"


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class LeadIn(BaseModel):
    """Inbound lead payload. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    company_name: Annotated[str, StringConstraints(min_length=2, max_length=120)]
    contact_name: Name
    contact_email: Annotated[str, StringConstraints(max_length=254)]
    company_size: CompanySize
    industry: Industry
    service_interest: ServiceLine
    budget_band: BudgetBand
    timeline: Timeline
    source: LeadSource = LeadSource.WEB_FORM
    message: Annotated[str, StringConstraints(min_length=10, max_length=5000)]

    @field_validator("contact_email")
    @classmethod
    def _synthetic_email(cls, value: str) -> str:
        email = normalize_email(value)
        domain = email_domain(email)
        if domain is None:
            raise ValueError("not a valid email address")
        if not is_synthetic_domain(domain):
            raise ValueError(
                "only synthetic addresses are accepted (example.com/.net/.org or a "
                ".example/.test/.invalid domain)"
            )
        return email

    @field_validator("company_name")
    @classmethod
    def _company_has_key(cls, value: str) -> str:
        if not company_key(value):
            raise ValueError("company name must contain letters or digits")
        return value


class ScoreComponent(BaseModel):
    rule: str
    input: str
    points: int
    max_points: int


class ScoreResult(BaseModel):
    rules_version: str
    total: int = Field(ge=0, le=100)
    tier: Tier
    route: Route
    breakdown: list[ScoreComponent]


# --- M2: advisory qualification and drafting -------------------------------------------------


class Urgency(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AssessmentFlag(StrEnum):
    REQUESTS_PRICING = "requests_pricing"
    OFF_TOPIC = "off_topic"
    UNCLEAR_NEED = "unclear_need"
    # Set only by the deterministic pre-screen, never accepted from model output.
    POSSIBLE_PROMPT_INJECTION = "possible_prompt_injection"


class AssessmentSource(StrEnum):
    LLM = "llm"
    RULES = "rules"


class DraftGenerator(StrEnum):
    TEMPLATE = "template"
    LLM = "llm"
    HUMAN_EDIT = "human_edit"


class Assessment(BaseModel):
    """Advisory qualification. Never feeds scoring, routing, or approval."""

    source: AssessmentSource
    summary: str
    service_line: ServiceLine
    urgency: Urgency
    flags: list[AssessmentFlag]


class DraftContent(BaseModel):
    subject: str
    body: str
