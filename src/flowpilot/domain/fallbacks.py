"""Deterministic fallbacks used whenever the LLM is disabled, unavailable, or untrusted.

Pure functions of the stored lead. Template drafts contain no prices, links, or promises, and
pass the same output guards as LLM drafts.
"""

from __future__ import annotations

import re

from flowpilot.domain.models import (
    Assessment,
    AssessmentFlag,
    AssessmentSource,
    DraftContent,
    Route,
    ServiceLine,
    Timeline,
    Urgency,
)

SIGNATURE = "Best regards,\nNorthstar Digital Solutions"
SUMMARY_MAX = 300

TIMELINE_URGENCY = {
    Timeline.IMMEDIATE: Urgency.HIGH,
    Timeline.M1_3: Urgency.MEDIUM,
    Timeline.M3_6: Urgency.LOW,
    Timeline.EXPLORING: Urgency.LOW,
}

_PRICING = re.compile(r"\b(price|pricing|cost|costs|quote|quotes|rate|rates|budget)\b", re.I)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s")
_NAME_TOKEN = re.compile(r"[^A-Za-z'-]")

ROUTE_NEXT_STEP = {
    Route.PARTNER_REVIEW: (
        "One of our partners would like to schedule a short discovery call to understand your "
        "goals and constraints. Could you share two or three times that suit you over the next "
        "week?"
    ),
    Route.SALES_QUEUE: (
        "A member of our team will review your requirements and follow up within two business "
        "days to suggest next steps."
    ),
    Route.NURTURE: (
        "We have noted your interest and will keep you informed about relevant work in this "
        "area. When your plans take shape, simply reply to this message and we will arrange a "
        "conversation."
    ),
}


def service_label(service: ServiceLine) -> str:
    return service.value.replace("_", " ")


def first_name(contact_name: str) -> str:
    """Conservative salutation: first token, letters/apostrophes/hyphens only."""
    tokens = contact_name.split()
    cleaned = _NAME_TOKEN.sub("", tokens[0])[:40] if tokens else ""
    return cleaned or "there"


def _first_sentence(text: str, limit: int) -> str:
    sentence = _SENTENCE_END.split(" ".join(text.split()), maxsplit=1)[0]
    return sentence if len(sentence) <= limit else sentence[: limit - 3].rstrip() + "..."


def rules_assessment(
    *,
    company_size: str,
    industry: str,
    service_interest: ServiceLine,
    timeline: Timeline,
    message: str,
    injection_suspected: bool,
) -> Assessment:
    flags: list[AssessmentFlag] = []
    if _PRICING.search(message):
        flags.append(AssessmentFlag.REQUESTS_PRICING)
    if service_interest is ServiceLine.OTHER or len(message.strip()) < 40:
        flags.append(AssessmentFlag.UNCLEAR_NEED)

    need = (
        "with an unspecified service need"
        if service_interest is ServiceLine.OTHER
        else f"interested in {service_label(service_interest)}"
    )
    prefix = f"{company_size}-person {industry.replace('_', ' ')} company {need}."
    if injection_suspected:
        # Never echo suspected-hostile text into a field a human will read as our analysis.
        flags.append(AssessmentFlag.POSSIBLE_PROMPT_INJECTION)
        summary = prefix + " Message withheld from summary: possible prompt injection."
    else:
        summary = f"{prefix} {_first_sentence(message, SUMMARY_MAX - len(prefix) - 1)}"

    return Assessment(
        source=AssessmentSource.RULES,
        summary=summary[:SUMMARY_MAX],
        service_line=service_interest,
        urgency=TIMELINE_URGENCY[timeline],
        flags=flags,
    )


def template_draft(
    *, contact_name: str, service_interest: ServiceLine, route: Route
) -> DraftContent:
    if service_interest is ServiceLine.OTHER:
        subject, topic = "Re: your enquiry", "your enquiry"
    else:
        label = service_label(service_interest)
        subject, topic = f"Re: your {label} enquiry", label
    body = (
        f"Hi {first_name(contact_name)},\n\n"
        f"Thank you for contacting Northstar Digital Solutions about {topic}. "
        f"{ROUTE_NEXT_STEP[route]}\n\n"
        f"{SIGNATURE}"
    )
    return DraftContent(subject=subject, body=body)
