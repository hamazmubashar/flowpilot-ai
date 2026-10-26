"""Individually authenticated human reviewers (M4.1).

Each reviewer gets a random 256-bit token, shown once by `flowpilot-admin reviewer add`; only its
SHA-256 is stored. A fast hash is sufficient because tokens are random, not user-chosen
passwords. Tokens are never logged, audited, or included in errors.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from dataclasses import dataclass

from flowpilot.db import repository as repo
from flowpilot.db.connection import transaction
from flowpilot.services.intake import Clock, iso, utc_now

TOKEN_PREFIX = "fpr_"  # noqa: S105 - public prefix that marks reviewer tokens, not a secret
# Same character set as the X-Actor audit label, because the name becomes the audit actor.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.:@-]{1,64}$")
ADMIN_ACTOR = "admin-cli"


class ReviewerError(Exception):
    """Invalid reviewer management request. Messages never contain a token."""


@dataclass(frozen=True)
class ReviewerIdentity:
    id: int
    name: str


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def authenticate(conn: sqlite3.Connection, token: str | None) -> ReviewerIdentity | None:
    """The active reviewer holding `token`, or None (missing, unknown, or revoked)."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    row = repo.get_active_reviewer_by_token_hash(conn, hash_token(token))
    return None if row is None else ReviewerIdentity(id=row["id"], name=row["name"])


TOKEN_PATTERN = re.compile(r"^fpr_[A-Za-z0-9_-]{40,128}$")


def add_reviewer(
    conn: sqlite3.Connection, name: str, *, clock: Clock = utc_now, token: str | None = None
) -> tuple[ReviewerIdentity, str]:
    """Create a reviewer; returns the identity and the plaintext token, which is not stored.

    `token` is normally generated here. The public demo passes an operator-chosen token so its
    demo reviewer survives scheduled resets (ADR 0005); it must look like a generated one."""
    if not NAME_PATTERN.match(name):
        raise ReviewerError(f"invalid reviewer name {name!r}: use 1-64 of A-Z a-z 0-9 _ . : @ -")
    if token is not None and not TOKEN_PATTERN.match(token):
        raise ReviewerError(
            "a supplied reviewer token must be fpr_ plus 40-128 URL-safe characters"
        )
    token = token or TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = iso(clock())
    with transaction(conn):
        if repo.get_reviewer_by_name(conn, name) is not None:
            raise ReviewerError(f"reviewer {name!r} already exists")
        reviewer_id = repo.insert_reviewer(conn, name=name, token_sha256=hash_token(token), now=now)
        repo.insert_audit(
            conn,
            occurred_at=now,
            actor=ADMIN_ACTOR,
            action="reviewer.added",
            entity_type="reviewer",
            entity_id=reviewer_id,
            after={"name": name},
        )
    return ReviewerIdentity(id=reviewer_id, name=name), token


def revoke_reviewer(conn: sqlite3.Connection, name: str, *, clock: Clock = utc_now) -> None:
    now = iso(clock())
    with transaction(conn):
        row = repo.get_reviewer_by_name(conn, name)
        if row is None:
            raise ReviewerError(f"reviewer {name!r} does not exist")
        if row["revoked_at"] is not None:
            raise ReviewerError(f"reviewer {name!r} is already revoked")
        repo.revoke_reviewer(conn, row["id"], now)
        repo.insert_audit(
            conn,
            occurred_at=now,
            actor=ADMIN_ACTOR,
            action="reviewer.revoked",
            entity_type="reviewer",
            entity_id=row["id"],
            before={"revoked_at": None},
            after={"revoked_at": now},
        )
