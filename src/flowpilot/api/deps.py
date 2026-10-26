"""Request-scoped dependencies: authentication, actor identity, DB connection."""

from __future__ import annotations

import hmac
import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from flowpilot.db.connection import connect
from flowpilot.services.reviewers import ReviewerIdentity, authenticate
from flowpilot.settings import Settings

ACTOR_PATTERN = r"^[A-Za-z0-9_.:@-]{1,64}$"
DEFAULT_ACTOR = "api-client"

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_reviewer_token_header = APIKeyHeader(name="X-Reviewer-Token", auto_error=False)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


# n8n's key may make only the calls its workflows are validated for (tests/test_n8n_workflows.py).
N8N_ROUTES = frozenset(
    {
        ("GET", "/leads"),
        ("POST", "/leads"),
        ("POST", "/leads/{lead_id}/qualification"),
        ("POST", "/leads/{lead_id}/drafts"),
    }
)


def _client_for(settings, provided: str | None) -> str | None:
    """The name of the client whose key matches; every key is compared in constant time."""
    if provided is None:
        return None
    candidate = provided.encode("utf-8")
    match = None
    for name, key in (
        ("default", settings.api_key),
        ("console", settings.console_api_key),
        ("n8n", settings.n8n_api_key),
    ):
        if key is not None and hmac.compare_digest(candidate, key.encode("utf-8")):
            match = name
    return match


def require_api_key(
    request: Request,
    provided: Annotated[str | None, Depends(_api_key_header)],
) -> None:
    client = _client_for(get_settings(request), provided)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthorized", "message": "missing or invalid X-API-Key"},
            headers={"WWW-Authenticate": "APIKey"},
        )
    request.state.api_client = client
    if client == "n8n":
        route = getattr(request.scope.get("route"), "path", "")
        if (request.method, route) not in N8N_ROUTES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": "this API key may not call this endpoint"},
            )


def get_actor(
    x_actor: Annotated[str | None, Header(pattern=ACTOR_PATTERN)] = None,
) -> str:
    """Caller-declared actor label for the audit log (trusted only because the caller is
    authenticated). Review decisions ignore it and use the authenticated reviewer (M4.1)."""
    return x_actor or DEFAULT_ACTOR


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = connect(get_settings(request).db_path)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
Actor = Annotated[str, Depends(get_actor)]


def require_reviewer(
    conn: Conn,
    token: Annotated[str | None, Depends(_reviewer_token_header)],
) -> ReviewerIdentity:
    """Human reviewer gate (M4.1), on top of the API key: the caller must present their own
    active `X-Reviewer-Token`. The reviewer's identity comes from the database, never from a
    caller-declared header. Automated clients such as n8n hold no token and are refused."""
    reviewer = authenticate(conn, token)
    if reviewer is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "reviewer_required", "message": "missing or invalid X-Reviewer-Token"},
        )
    return reviewer


Reviewer = Annotated[ReviewerIdentity, Depends(require_reviewer)]
