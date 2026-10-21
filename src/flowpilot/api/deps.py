"""Request-scoped dependencies: authentication, actor identity, DB connection."""

from __future__ import annotations

import hmac
import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from flowpilot.db.connection import connect
from flowpilot.settings import Settings

ACTOR_PATTERN = r"^[A-Za-z0-9_.:@-]{1,64}$"
DEFAULT_ACTOR = "api-client"

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_reviewer_key_header = APIKeyHeader(name="X-Reviewer-Key", auto_error=False)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def require_api_key(
    request: Request,
    provided: Annotated[str | None, Depends(_api_key_header)],
) -> None:
    expected = get_settings(request).api_key
    if provided is None or not hmac.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthorized", "message": "missing or invalid X-API-Key"},
            headers={"WWW-Authenticate": "APIKey"},
        )


def get_actor(
    x_actor: Annotated[str | None, Header(pattern=ACTOR_PATTERN)] = None,
) -> str:
    """Caller-declared actor label for the audit log (trusted only because the caller is
    authenticated). Per-user identity arrives with the human approval flow in M4."""
    return x_actor or DEFAULT_ACTOR


def require_reviewer(
    request: Request,
    provided: Annotated[str | None, Depends(_reviewer_key_header)],
    x_actor: Annotated[str, Header(pattern=ACTOR_PATTERN)],
) -> str:
    """Human reviewer gate (M4), on top of the API key. Returns the reviewer's name from the
    required `X-Actor` header. Only holders of the separate reviewer key pass; automated clients
    such as n8n hold just the API key and are refused."""
    expected = get_settings(request).reviewer_key
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "review_disabled", "message": "FLOWPILOT_REVIEWER_KEY is not set"},
        )
    if provided is None or not hmac.compare_digest(
        provided.encode("utf-8"), expected.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "reviewer_required", "message": "missing or invalid X-Reviewer-Key"},
        )
    return x_actor


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = connect(get_settings(request).db_path)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
Actor = Annotated[str, Depends(get_actor)]
Reviewer = Annotated[str, Depends(require_reviewer)]
