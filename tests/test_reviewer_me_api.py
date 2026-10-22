"""M5 login check: `GET /reviewers/me` identifies the reviewer holding `X-Reviewer-Token`.

It reuses the review-decision gate (API key, then an active reviewer token) and writes nothing.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flowpilot.db.connection import connect
from flowpilot.services.reviewers import add_reviewer, revoke_reviewer
from tests.conftest import API_KEY, FIXED_NOW

TABLES = ("reviewers", "audit_log", "approvals", "outbox", "idempotency_keys")


def make_reviewer(db_path: Path, name: str) -> str:
    conn = connect(db_path)
    try:
        return add_reviewer(conn, name, clock=lambda: FIXED_NOW)[1]
    finally:
        conn.close()


def snapshot(db_path: Path) -> dict[str, list[tuple]]:
    conn = connect(db_path)
    try:
        return {
            t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")]  # noqa: S608
            for t in TABLES
        }
    finally:
        conn.close()


def me(client: TestClient, **headers: str):
    return client.get("/reviewers/me", headers=headers)


def test_valid_token_returns_reviewer_identity_and_writes_nothing(
    client: TestClient, db_path: Path
) -> None:
    make_reviewer(db_path, "reviewer:blake")
    token = make_reviewer(db_path, "reviewer:avery")
    before = snapshot(db_path)

    response = me(client, **{"X-API-Key": API_KEY, "X-Reviewer-Token": token})

    assert response.status_code == 200
    body = response.json()
    assert body == {"id": body["id"], "name": "reviewer:avery"}
    assert token not in response.text
    assert snapshot(db_path) == before


def test_missing_token_is_403(client: TestClient) -> None:
    response = me(client, **{"X-API-Key": API_KEY})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "reviewer_required"


@pytest.mark.parametrize("token", ["fpr_unknown-token-value", "not-a-reviewer-token", API_KEY])
def test_unknown_token_is_403(client: TestClient, db_path: Path, token: str) -> None:
    make_reviewer(db_path, "reviewer:avery")
    response = me(client, **{"X-API-Key": API_KEY, "X-Reviewer-Token": token})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "reviewer_required"
    assert token not in response.text


def test_revoked_token_is_403(client: TestClient, db_path: Path) -> None:
    token = make_reviewer(db_path, "reviewer:avery")
    headers = {"X-API-Key": API_KEY, "X-Reviewer-Token": token}
    assert me(client, **headers).status_code == 200

    conn = connect(db_path)
    try:
        revoke_reviewer(conn, "reviewer:avery", clock=lambda: FIXED_NOW)
    finally:
        conn.close()

    response = me(client, **headers)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "reviewer_required"


@pytest.mark.parametrize("api_key", [None, API_KEY + "x"])
def test_missing_or_wrong_api_key_is_401_even_with_valid_token(
    client: TestClient, db_path: Path, api_key: str | None
) -> None:
    token = make_reviewer(db_path, "reviewer:avery")
    headers = {"X-Reviewer-Token": token}
    if api_key is not None:
        headers["X-API-Key"] = api_key
    response = me(client, **headers)
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "unauthorized"
