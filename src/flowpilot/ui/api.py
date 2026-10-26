"""FastAPI client for the reviewer console. No Streamlit import, so it is tested in the default
suite.

The API key and reviewer tokens stay in the Streamlit server process; they are sent only as
request headers to the loopback API and never appear in results, errors or reprs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from flowpilot.settings import (
    ENV_PREFIX,
    LOOPBACK_HOSTS,
    MIN_API_KEY_LENGTH,
    SettingsError,
    _merged_env,
)

DEFAULT_API_URL = "http://127.0.0.1:8000"
REQUEST_TIMEOUT_S = 5.0
MAX_TOKEN_LENGTH = 256


PUBLIC_DEMO_API_HOST = "api"  # the private Compose service name (ADR 0005)


@dataclass(frozen=True)
class UIConfig:
    api_url: str
    api_key: str = field(repr=False)
    public_demo: bool = False  # FLOWPILOT_PUBLIC_DEMO=1: the API is the private `api` service

    def __post_init__(self) -> None:
        parts = urlsplit(self.api_url)
        try:
            port = parts.port
        except ValueError:
            port = -1
        if (
            parts.scheme not in {"http", "https"}
            or not (
                parts.hostname in LOOPBACK_HOSTS
                or (self.public_demo and parts.hostname == PUBLIC_DEMO_API_HOST)
            )
            or parts.username
            or parts.password
            or port == -1
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
        ):
            raise SettingsError(
                f"FLOWPILOT_API_URL={self.api_url!r} must be a bare http(s) URL on a loopback "
                f"address, e.g. {DEFAULT_API_URL}."
            )
        if len(self.api_key) < MIN_API_KEY_LENGTH:
            raise SettingsError(
                "FLOWPILOT_API_KEY is missing or too short; the console needs the same key as "
                "the API."
            )


def load_ui_config(
    environ: Mapping[str, str] | None = None, env_file: Path | None = Path(".env")
) -> UIConfig:
    """Console settings from `env_file` overlaid by `environ` (real env vars win)."""
    merged = _merged_env(environ, env_file)
    return UIConfig(
        api_url=(merged.get(ENV_PREFIX + "API_URL") or DEFAULT_API_URL).rstrip("/"),
        api_key=merged.get(ENV_PREFIX + "API_KEY") or "",
        public_demo=merged.get(ENV_PREFIX + "PUBLIC_DEMO") == "1",
    )


@dataclass(frozen=True)
class ReviewerIdentity:
    id: int
    name: str


class LoginOutcome(StrEnum):
    OK = "ok"
    INVALID_TOKEN = "invalid_token"  # noqa: S105 - outcome label; missing/unknown/revoked (403)
    API_KEY_REJECTED = "api_key_rejected"  # console misconfigured (API 401)
    API_UNAVAILABLE = "api_unavailable"  # connection error, timeout or 5xx
    UNEXPECTED = "unexpected"


@dataclass(frozen=True)
class LoginResult:
    outcome: LoginOutcome
    reviewer: ReviewerIdentity | None = None


def _header_safe(token: str) -> bool:
    return 0 < len(token) <= MAX_TOKEN_LENGTH and token.isascii() and token.isprintable()


def verify_reviewer(
    config: UIConfig, token: str, *, transport: httpx.BaseTransport | None = None
) -> LoginResult:
    """Ask the API who holds `token` (`GET /reviewers/me`, read-only)."""
    token = token.strip()
    if not _header_safe(token):
        return LoginResult(LoginOutcome.INVALID_TOKEN)
    headers = {"X-API-Key": config.api_key, "X-Reviewer-Token": token}
    try:
        # trust_env=False: ignore proxy variables so the request can only reach the loopback API.
        with httpx.Client(
            base_url=config.api_url,
            timeout=REQUEST_TIMEOUT_S,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        ) as client:
            response = client.get("/reviewers/me", headers=headers)
    except httpx.HTTPError:
        return LoginResult(LoginOutcome.API_UNAVAILABLE)

    if response.status_code == 403:
        return LoginResult(LoginOutcome.INVALID_TOKEN)
    if response.status_code == 401:
        return LoginResult(LoginOutcome.API_KEY_REJECTED)
    if response.status_code >= 500:
        return LoginResult(LoginOutcome.API_UNAVAILABLE)
    if response.status_code != 200:
        return LoginResult(LoginOutcome.UNEXPECTED)
    try:
        body = response.json()
        reviewer = ReviewerIdentity(id=body["id"], name=body["name"])
    except (ValueError, KeyError, TypeError):
        return LoginResult(LoginOutcome.UNEXPECTED)
    if not isinstance(reviewer.id, int) or not isinstance(reviewer.name, str):
        return LoginResult(LoginOutcome.UNEXPECTED)
    return LoginResult(LoginOutcome.OK, reviewer)


# --- Read-only data for the Dashboard (M5). These calls need only the API key: the console checks
# the reviewer's token with `verify_reviewer` before it fetches or shows any lead data.

PAGE_SIZE = 100  # the API's maximum `limit`
MAX_LEADS = 2000  # local demo cap; a larger pipeline is reported as truncated


class ApiOutcome(StrEnum):
    OK = "ok"
    API_KEY_REJECTED = "api_key_rejected"
    API_UNAVAILABLE = "api_unavailable"
    UNEXPECTED = "unexpected"
    # 404 on a collection endpoint: the running API predates it (started before an update).
    ENDPOINT_MISSING = "endpoint_missing"


@dataclass(frozen=True)
class LeadsSnapshot:
    outcome: ApiOutcome
    leads: tuple[dict, ...] = ()
    total: int = 0
    truncated: bool = False


def _client(config: UIConfig, transport: httpx.BaseTransport | None) -> httpx.Client:
    return httpx.Client(
        base_url=config.api_url,
        headers={"X-API-Key": config.api_key},
        timeout=REQUEST_TIMEOUT_S,
        trust_env=False,
        follow_redirects=False,
        transport=transport,
    )


def _failure(status_code: int) -> ApiOutcome:
    if status_code == 401:
        return ApiOutcome.API_KEY_REJECTED
    if status_code >= 500:
        return ApiOutcome.API_UNAVAILABLE
    return ApiOutcome.UNEXPECTED


def fetch_leads(
    config: UIConfig, *, transport: httpx.BaseTransport | None = None, max_leads: int = MAX_LEADS
) -> LeadsSnapshot:
    """Every lead from `GET /leads`, page by page (the API orders them by id)."""
    leads: list[dict] = []
    try:
        with _client(config, transport) as client:
            while True:
                response = client.get("/leads", params={"limit": PAGE_SIZE, "offset": len(leads)})
                if response.status_code != 200:
                    return LeadsSnapshot(_failure(response.status_code))
                body = response.json()
                items, total = body["items"], body["total"]
                if not isinstance(items, list) or not isinstance(total, int):
                    return LeadsSnapshot(ApiOutcome.UNEXPECTED)
                leads.extend(items)
                if not items or len(leads) >= total:
                    return LeadsSnapshot(ApiOutcome.OK, tuple(leads), total)
                if len(leads) >= max_leads:
                    return LeadsSnapshot(ApiOutcome.OK, tuple(leads), total, truncated=True)
    except httpx.HTTPError:
        return LeadsSnapshot(ApiOutcome.API_UNAVAILABLE)
    except (ValueError, KeyError, TypeError):
        return LeadsSnapshot(ApiOutcome.UNEXPECTED)


def fetch_audit(
    config: UIConfig, lead_ids: list[int], *, transport: httpx.BaseTransport | None = None
) -> tuple[ApiOutcome, dict[int, list[dict]]]:
    """`GET /leads/{id}/audit` for each id. A lead deleted in between (404) is skipped."""
    entries: dict[int, list[dict]] = {}
    try:
        with _client(config, transport) as client:
            for lead_id in lead_ids:
                response = client.get(f"/leads/{int(lead_id)}/audit")
                if response.status_code == 404:
                    continue
                if response.status_code != 200:
                    return _failure(response.status_code), {}
                body = response.json()
                if not isinstance(body, list):
                    return ApiOutcome.UNEXPECTED, {}
                entries[int(lead_id)] = body
    except httpx.HTTPError:
        return ApiOutcome.API_UNAVAILABLE, {}
    except ValueError:
        return ApiOutcome.UNEXPECTED, {}
    return ApiOutcome.OK, entries


# --- Leads page (M5): one server-side page at a time, totals from `total`, and lead creation.

LEADS_PAGE_SIZE = 25


@dataclass(frozen=True)
class LeadPageResult:
    outcome: ApiOutcome
    items: tuple[dict, ...] = ()
    total: int = 0


def _page(client: httpx.Client, params: dict) -> LeadPageResult:
    response = client.get("/leads", params={k: v for k, v in params.items() if v is not None})
    if response.status_code != 200:
        return LeadPageResult(_failure(response.status_code))
    body = response.json()
    items, total = body["items"], body["total"]
    if not isinstance(items, list) or not isinstance(total, int):
        return LeadPageResult(ApiOutcome.UNEXPECTED)
    return LeadPageResult(ApiOutcome.OK, tuple(items), total)


def fetch_lead_page(
    config: UIConfig,
    *,
    status: str | None = None,
    tier: str | None = None,
    offset: int = 0,
    limit: int = LEADS_PAGE_SIZE,
    transport: httpx.BaseTransport | None = None,
) -> LeadPageResult:
    """One page of `GET /leads`; filtering and paging happen on the server."""
    params = {"status": status, "tier": tier, "limit": limit, "offset": offset}
    try:
        with _client(config, transport) as client:
            return _page(client, params)
    except httpx.HTTPError:
        return LeadPageResult(ApiOutcome.API_UNAVAILABLE)
    except (ValueError, KeyError, TypeError):
        return LeadPageResult(ApiOutcome.UNEXPECTED)


def fetch_lead_totals(
    config: UIConfig,
    filters: dict[str, dict[str, str]],
    *,
    transport: httpx.BaseTransport | None = None,
) -> tuple[ApiOutcome, dict[str, int]]:
    """`total` for each named filter, using `limit=1` so no records are transferred in bulk."""
    totals: dict[str, int] = {}
    try:
        with _client(config, transport) as client:
            for name, params in filters.items():
                result = _page(client, {**params, "limit": 1, "offset": 0})
                if result.outcome is not ApiOutcome.OK:
                    return result.outcome, {}
                totals[name] = result.total
    except httpx.HTTPError:
        return ApiOutcome.API_UNAVAILABLE, {}
    except (ValueError, KeyError, TypeError):
        return ApiOutcome.UNEXPECTED, {}
    return ApiOutcome.OK, totals


class CreateOutcome(StrEnum):
    CREATED = "created"
    DUPLICATE = "duplicate"  # same email and company: the existing lead is returned (200)
    INVALID = "invalid"  # 422 with field errors
    KEY_REUSED = "key_reused"  # 422: idempotency key sent earlier with another payload
    API_KEY_REJECTED = "api_key_rejected"
    API_UNAVAILABLE = "api_unavailable"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True)
class CreateResult:
    outcome: CreateOutcome
    lead: dict | None = None
    replayed: bool = False
    errors: tuple[tuple[str, str], ...] = ()  # (field, message)


def _field_errors(detail: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(detail, list):
        return ()
    errors = []
    for error in detail:
        if not isinstance(error, dict):
            continue
        loc = error.get("loc") or ()
        field = str(loc[-1]) if loc and loc[0] == "body" and len(loc) > 1 else "request"
        message = str(error.get("msg", "invalid value")).removeprefix("Value error, ")
        errors.append((field, message))
    return tuple(errors)


def create_lead(
    config: UIConfig,
    payload: dict,
    *,
    actor: str,
    idempotency_key: str,
    transport: httpx.BaseTransport | None = None,
) -> CreateResult:
    """`POST /leads`. The idempotency key makes a retried or double submission safe."""
    headers = {"Idempotency-Key": idempotency_key, "X-Actor": actor}
    try:
        with _client(config, transport) as client:
            response = client.post("/leads", json=payload, headers=headers)
            body = response.json()
    except httpx.HTTPError:
        return CreateResult(CreateOutcome.API_UNAVAILABLE)
    except ValueError:
        return CreateResult(CreateOutcome.UNEXPECTED)
    if response.status_code in (200, 201):
        try:
            lead, duplicate, replayed = body["lead"], body["duplicate"], body["replayed"]
            int(lead["id"])
        except (KeyError, TypeError, ValueError):
            return CreateResult(CreateOutcome.UNEXPECTED)
        outcome = CreateOutcome.DUPLICATE if duplicate else CreateOutcome.CREATED
        return CreateResult(outcome, lead=lead, replayed=bool(replayed))
    if response.status_code == 422:
        detail = body.get("detail") if isinstance(body, dict) else None
        if isinstance(detail, dict) and detail.get("code") == "idempotency_key_reused":
            return CreateResult(CreateOutcome.KEY_REUSED)
        return CreateResult(CreateOutcome.INVALID, errors=_field_errors(detail))
    if response.status_code == 401:
        return CreateResult(CreateOutcome.API_KEY_REJECTED)
    if response.status_code >= 500:
        return CreateResult(CreateOutcome.API_UNAVAILABLE)
    return CreateResult(CreateOutcome.UNEXPECTED)


# --- Draft Responses page (M5): versions, the advisory assessment, and the human decision.


@dataclass(frozen=True)
class ItemResult:
    outcome: ApiOutcome
    data: object = None  # list of drafts, an assessment dict, or None when there is none


def fetch_drafts(
    config: UIConfig, lead_id: int, *, transport: httpx.BaseTransport | None = None
) -> ItemResult:
    """`GET /leads/{id}/drafts`: every version, oldest first."""
    try:
        with _client(config, transport) as client:
            response = client.get(f"/leads/{int(lead_id)}/drafts")
            if response.status_code != 200:
                return ItemResult(_failure(response.status_code))
            body = response.json()
    except httpx.HTTPError:
        return ItemResult(ApiOutcome.API_UNAVAILABLE)
    except ValueError:
        return ItemResult(ApiOutcome.UNEXPECTED)
    if not isinstance(body, list) or not all(isinstance(d, dict) for d in body):
        return ItemResult(ApiOutcome.UNEXPECTED)
    return ItemResult(ApiOutcome.OK, body)


def fetch_assessment(
    config: UIConfig, lead_id: int, *, transport: httpx.BaseTransport | None = None
) -> ItemResult:
    """`GET /leads/{id}/qualification`; a lead that was never qualified gives data=None."""
    try:
        with _client(config, transport) as client:
            response = client.get(f"/leads/{int(lead_id)}/qualification")
            body = response.json()
    except httpx.HTTPError:
        return ItemResult(ApiOutcome.API_UNAVAILABLE)
    except ValueError:
        return ItemResult(ApiOutcome.UNEXPECTED)
    if response.status_code == 404 and isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, dict) and detail.get("code") == "assessment_not_found":
            return ItemResult(ApiOutcome.OK, None)
    if response.status_code != 200:
        return ItemResult(_failure(response.status_code))
    if not isinstance(body, dict):
        return ItemResult(ApiOutcome.UNEXPECTED)
    return ItemResult(ApiOutcome.OK, body)


class DecisionOutcome(StrEnum):
    DECIDED = "decided"
    REVIEWER_REJECTED = "reviewer_rejected"  # 403: token revoked or unknown
    SUPERSEDED = "superseded"  # 409 draft_superseded
    ALREADY_DECIDED = "already_decided"  # 409 draft_already_decided
    NOT_REVIEWABLE = "not_reviewable"  # 409 lead_not_reviewable
    NOT_FOUND = "not_found"  # 404 lead or draft
    INVALID = "invalid"  # 422, including a reused idempotency key
    API_KEY_REJECTED = "api_key_rejected"
    API_UNAVAILABLE = "api_unavailable"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True)
class DecisionResult:
    outcome: DecisionOutcome
    approval: dict | None = None
    message: str = ""  # the API's own explanation, for conflicts and validation errors
    replayed: bool = False


_DECISION_CONFLICTS = {
    "draft_superseded": DecisionOutcome.SUPERSEDED,
    "draft_already_decided": DecisionOutcome.ALREADY_DECIDED,
    "lead_not_reviewable": DecisionOutcome.NOT_REVIEWABLE,
}


def decide_draft(
    config: UIConfig,
    token: str,
    lead_id: int,
    version: int,
    *,
    decision: str,
    reason: str | None,
    idempotency_key: str,
    transport: httpx.BaseTransport | None = None,
) -> DecisionResult:
    """`POST /leads/{id}/drafts/{version}/approval` as the signed-in reviewer.

    The reviewer's identity comes from the token on the server, never from this client.
    Approval enqueues one simulated outbox entry; nothing is ever sent.
    """
    body: dict[str, str] = {"decision": decision}
    if reason:
        body["reason"] = reason
    headers = {"X-Reviewer-Token": token, "Idempotency-Key": idempotency_key}
    try:
        with _client(config, transport) as client:
            response = client.post(
                f"/leads/{int(lead_id)}/drafts/{int(version)}/approval", json=body, headers=headers
            )
            payload = response.json()
    except httpx.HTTPError:
        return DecisionResult(DecisionOutcome.API_UNAVAILABLE)
    except ValueError:
        return DecisionResult(DecisionOutcome.UNEXPECTED)
    detail = payload.get("detail") if isinstance(payload, dict) else None
    code = detail.get("code") if isinstance(detail, dict) else None
    message = str(detail.get("message", "")) if isinstance(detail, dict) else ""
    if response.status_code == 201:
        try:
            confirmed = payload["decision"] in ("approved", "rejected") and int(payload["version"])
        except (KeyError, TypeError, ValueError):
            confirmed = False
        if not confirmed:  # never report a decision the API did not clearly confirm
            return DecisionResult(DecisionOutcome.UNEXPECTED)
        replayed = response.headers.get("Idempotent-Replayed") == "true"
        return DecisionResult(DecisionOutcome.DECIDED, approval=payload, replayed=replayed)
    if response.status_code == 403:
        return DecisionResult(DecisionOutcome.REVIEWER_REJECTED)
    if response.status_code == 409 and code in _DECISION_CONFLICTS:
        return DecisionResult(_DECISION_CONFLICTS[code], message=message)
    if response.status_code == 404:
        return DecisionResult(DecisionOutcome.NOT_FOUND, message=message)
    if response.status_code == 422:
        if not message and isinstance(detail, list):
            message = "; ".join(m for _, m in _field_errors(detail))
        return DecisionResult(DecisionOutcome.INVALID, message=message)
    if response.status_code == 401:
        return DecisionResult(DecisionOutcome.API_KEY_REJECTED)
    if response.status_code >= 500:
        return DecisionResult(DecisionOutcome.API_UNAVAILABLE)
    return DecisionResult(DecisionOutcome.UNEXPECTED)


# --- Outbox (Simulated) page (M5): read-only `GET /outbox`.

OUTBOX_FIELDS = (
    "id", "lead_id", "contact_name", "company_name", "draft_version", "decided_by",
    "decided_at", "channel", "recipient", "subject", "body", "status", "created_at",
)  # fmt: skip


def fetch_outbox(
    config: UIConfig,
    *,
    status: str | None = None,
    offset: int = 0,
    limit: int = LEADS_PAGE_SIZE,
    transport: httpx.BaseTransport | None = None,
) -> LeadPageResult:
    """One page of simulated outbox entries, newest first. Any entry that is not on the
    `simulated` channel, or lacks a field, makes the whole response unexpected."""
    params = {"status": status, "limit": limit, "offset": offset}
    try:
        with _client(config, transport) as client:
            response = client.get(
                "/outbox", params={k: v for k, v in params.items() if v is not None}
            )
            if response.status_code == 404:  # a collection route cannot 404 unless it is absent
                return LeadPageResult(ApiOutcome.ENDPOINT_MISSING)
            if response.status_code != 200:
                return LeadPageResult(_failure(response.status_code))
            body = response.json()
        items, total = body["items"], body["total"]
    except httpx.HTTPError:
        return LeadPageResult(ApiOutcome.API_UNAVAILABLE)
    except (ValueError, KeyError, TypeError):
        return LeadPageResult(ApiOutcome.UNEXPECTED)
    if not isinstance(items, list) or not isinstance(total, int):
        return LeadPageResult(ApiOutcome.UNEXPECTED)
    for item in items:
        if (
            not isinstance(item, dict)
            or any(field not in item for field in OUTBOX_FIELDS)
            or item["channel"] != "simulated"
        ):
            return LeadPageResult(ApiOutcome.UNEXPECTED)
    return LeadPageResult(ApiOutcome.OK, tuple(items), total)


# --- Settings page (M5): public `GET /health`.


@dataclass(frozen=True)
class HealthResult:
    outcome: ApiOutcome
    status: str = ""  # "ok" or "degraded"
    database: str = ""  # "ok", "migrations_pending" or "unavailable"


def fetch_health(config: UIConfig, *, transport: httpx.BaseTransport | None = None) -> HealthResult:
    """`GET /health` (no key needed). A degraded API answers 503 with the same body."""
    try:
        with _client(config, transport) as client:
            response = client.get("/health")
            body = response.json()
    except httpx.HTTPError:
        return HealthResult(ApiOutcome.API_UNAVAILABLE)
    except ValueError:
        return HealthResult(ApiOutcome.UNEXPECTED)
    if response.status_code not in (200, 503) or not isinstance(body, dict):
        return HealthResult(ApiOutcome.UNEXPECTED)
    status, database = body.get("status"), body.get("database")
    if not isinstance(status, str) or not isinstance(database, str):
        return HealthResult(ApiOutcome.UNEXPECTED)
    return HealthResult(ApiOutcome.OK, status, database)
