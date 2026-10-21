"""M4/M4.1 acceptance: an individually authenticated human reviewer approves or rejects one
exact draft version.

Deciding requires the API key plus the reviewer's own token; the reviewer's identity comes from
the database, never from a caller-declared header. An automated client that holds only the API
key (n8n) cannot decide. Approval enqueues one simulated outbox row.
"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from flowpilot.db.connection import connect
from flowpilot.domain.models import DraftDecisionIn
from flowpilot.services.review import ReviewerNotActive, decide_draft
from flowpilot.services.reviewers import add_reviewer, authenticate, revoke_reviewer
from tests.conftest import API_KEY, FIXED_NOW

REVIEWER = "reviewer:avery"


def make_reviewer(db_path: Path, name: str = REVIEWER) -> str:
    conn = connect(db_path)
    try:
        return add_reviewer(conn, name, clock=lambda: FIXED_NOW)[1]
    finally:
        conn.close()


@pytest.fixture
def review_client(client: TestClient) -> TestClient:
    return client


@pytest.fixture
def token(db_path: Path) -> str:
    return make_reviewer(db_path)


@pytest.fixture
def reviewer_headers(auth: dict, token: str):
    def headers(**extra: str) -> dict[str, str]:
        return {**auth, "X-Reviewer-Token": token, **extra}

    return headers


def drafted_lead(client: TestClient, auth: dict, payload: dict, drafts: int = 1) -> int:
    lead_id = client.post("/leads", json=payload, headers=auth).json()["lead"]["id"]
    for _ in range(drafts):
        assert client.post(f"/leads/{lead_id}/drafts", headers=auth).status_code == 201
    return lead_id


def decide(client: TestClient, lead_id: int, version: int, headers: dict, **body: Any) -> Any:
    return client.post(
        f"/leads/{lead_id}/drafts/{version}/approval",
        json={"decision": "approved", **body},
        headers=headers,
    )


def rows(db_path: Path, table: str) -> list[dict]:
    conn = connect(db_path)
    try:
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")]  # noqa: S608
    finally:
        conn.close()


def lead_status(client: TestClient, auth: dict, lead_id: int) -> str:
    return client.get(f"/leads/{lead_id}", headers=auth).json()["status"]


def test_approval_records_human_decision_and_one_simulated_outbox_entry(
    review_client: TestClient, auth: dict, lead_payload: dict, db_path: Path, reviewer_headers
) -> None:
    lead_id = drafted_lead(review_client, auth, lead_payload)
    draft = review_client.get(f"/leads/{lead_id}/drafts", headers=auth).json()[0]

    response = decide(review_client, lead_id, 1, reviewer_headers(), reason="Looks right.")

    assert response.status_code == 201
    body = response.json()
    assert body["decision"] == "approved"
    assert (body["actor_type"], body["decided_by"]) == ("human", REVIEWER)
    assert (body["draft_id"], body["version"]) == (draft["id"], 1)
    assert body["outbox"]["channel"] == "simulated"
    assert body["outbox"]["status"] == "pending"
    assert lead_status(review_client, auth, lead_id) == "approved"
    [outbox] = rows(db_path, "outbox")
    assert (outbox["draft_id"], outbox["subject"], outbox["body"]) == (
        draft["id"],
        draft["subject"],
        draft["body"],
    )
    stored_email = review_client.get(f"/leads/{lead_id}", headers=auth).json()["contact_email"]
    assert outbox["recipient"] == stored_email
    assert outbox["sent_at"] is None
    actions = [(a["action"], a["actor"]) for a in rows(db_path, "audit_log")]
    assert actions[-2:] == [("lead.approved", REVIEWER), ("outbox.enqueued", REVIEWER)]


def test_rejection_records_decision_without_outbox_entry(
    review_client: TestClient, auth: dict, lead_payload: dict, db_path: Path, reviewer_headers
) -> None:
    lead_id = drafted_lead(review_client, auth, lead_payload)

    response = decide(
        review_client, lead_id, 1, reviewer_headers(), decision="rejected", reason="Too vague."
    )

    assert response.status_code == 201
    assert (response.json()["decision"], response.json()["outbox"]) == ("rejected", None)
    assert lead_status(review_client, auth, lead_id) == "rejected"
    assert rows(db_path, "outbox") == []
    assert rows(db_path, "audit_log")[-1]["action"] == "lead.rejected"


@pytest.mark.parametrize(
    "case",
    ["api-key-only (n8n)", "api-key-as-token", "unknown-token", "revoked-token"],
)
def test_without_an_active_reviewer_token_nothing_is_decided(
    review_client: TestClient, auth: dict, lead_payload: dict, db_path: Path, token: str, case: str
) -> None:
    lead_id = drafted_lead(review_client, auth, lead_payload)
    headers = {**auth, "X-Actor": REVIEWER}
    if case == "api-key-as-token":
        headers["X-Reviewer-Token"] = API_KEY
    elif case == "unknown-token":
        headers["X-Reviewer-Token"] = "fpr_" + "x" * 43
    elif case == "revoked-token":
        conn = connect(db_path)
        try:
            revoke_reviewer(conn, REVIEWER, clock=lambda: FIXED_NOW)
        finally:
            conn.close()
        headers["X-Reviewer-Token"] = token

    response = decide(review_client, lead_id, 1, headers)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "reviewer_required"
    assert token not in response.text
    assert rows(db_path, "approvals") == rows(db_path, "outbox") == []
    assert lead_status(review_client, auth, lead_id) == "drafted"


def test_identity_comes_from_the_token_not_x_actor(
    review_client: TestClient, auth: dict, lead_payload: dict, db_path: Path, reviewer_headers
) -> None:
    lead_id = drafted_lead(review_client, auth, lead_payload)

    response = decide(review_client, lead_id, 1, reviewer_headers(**{"X-Actor": "partner:spoof"}))

    assert response.status_code == 201
    [approval] = rows(db_path, "approvals")
    assert (approval["decided_by"], approval["reviewer_id"]) == (REVIEWER, 1)
    assert {a["actor"] for a in rows(db_path, "audit_log")[-2:]} == {REVIEWER}


def test_idempotency_keys_are_isolated_per_reviewer(
    review_client: TestClient, auth: dict, lead_payload: dict, db_path: Path, reviewer_headers
) -> None:
    lead_id = drafted_lead(review_client, auth, lead_payload)
    other = make_reviewer(db_path, "reviewer:blake")
    key = {"Idempotency-Key": "review:shared-key"}
    assert decide(review_client, lead_id, 1, reviewer_headers(**key)).status_code == 201

    response = decide(review_client, lead_id, 1, {**auth, "X-Reviewer-Token": other, **key})

    assert (response.status_code, response.json()["detail"]["code"]) == (
        422,
        "idempotency_key_reused",
    )
    assert len(rows(db_path, "approvals")) == 1


def test_only_the_latest_existing_draft_version_can_be_decided(
    review_client: TestClient, auth: dict, lead_payload: dict, db_path: Path, reviewer_headers
) -> None:
    lead_id = drafted_lead(review_client, auth, lead_payload, drafts=2)
    headers = reviewer_headers()

    stale = decide(review_client, lead_id, 1, headers)
    missing = decide(review_client, lead_id, 3, headers)

    assert (stale.status_code, stale.json()["detail"]["code"]) == (409, "draft_superseded")
    assert (missing.status_code, missing.json()["detail"]["code"]) == (404, "draft_not_found")
    assert rows(db_path, "approvals") == []
    approved = decide(review_client, lead_id, 2, headers)
    assert (approved.status_code, approved.json()["version"]) == (201, 2)


def test_a_decision_is_final(
    review_client: TestClient, auth: dict, lead_payload: dict, db_path: Path, reviewer_headers
) -> None:
    lead_id = drafted_lead(review_client, auth, lead_payload)
    headers = reviewer_headers()
    assert decide(review_client, lead_id, 1, headers).status_code == 201

    again = decide(review_client, lead_id, 1, headers, decision="rejected")

    assert (again.status_code, again.json()["detail"]["code"]) == (409, "draft_already_decided")
    assert len(rows(db_path, "approvals")) == len(rows(db_path, "outbox")) == 1
    assert lead_status(review_client, auth, lead_id) == "approved"


def test_idempotent_retry_replays_without_duplicates(
    review_client: TestClient, auth: dict, lead_payload: dict, db_path: Path, reviewer_headers
) -> None:
    lead_id = drafted_lead(review_client, auth, lead_payload)
    headers = reviewer_headers(**{"Idempotency-Key": "review:lead-1:v1"})

    first = decide(review_client, lead_id, 1, headers)
    retry = decide(review_client, lead_id, 1, headers)
    reused = decide(review_client, lead_id, 1, headers, decision="rejected")

    assert (first.status_code, retry.status_code) == (201, 201)
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert retry.json() == first.json()
    assert (reused.status_code, reused.json()["detail"]["code"]) == (422, "idempotency_key_reused")
    assert len(rows(db_path, "approvals")) == len(rows(db_path, "outbox")) == 1


@pytest.mark.parametrize("status", ["scored", "awaiting_approval", "closed"])
def test_lead_status_must_allow_review(
    review_client: TestClient,
    auth: dict,
    lead_payload: dict,
    db_path: Path,
    reviewer_headers,
    status: str,
) -> None:
    lead_id = drafted_lead(review_client, auth, lead_payload)
    conn = connect(db_path)
    try:
        conn.execute("UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))
    finally:
        conn.close()

    response = decide(review_client, lead_id, 1, reviewer_headers())

    if status == "awaiting_approval":
        assert response.status_code == 201
    else:
        assert (response.status_code, response.json()["detail"]["code"]) == (
            409,
            "lead_not_reviewable",
        )
        assert rows(db_path, "approvals") == []


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "auto_approved"},
        {"decision": "approved", "actor_type": "system"},
        {"decision": "approved", "reason": "x" * 501},
        {},
    ],
    ids=["unknown-decision", "extra-field", "reason-too-long", "empty"],
)
def test_invalid_decision_body_is_422(
    review_client: TestClient,
    auth: dict,
    lead_payload: dict,
    db_path: Path,
    reviewer_headers,
    body: dict,
) -> None:
    lead_id = drafted_lead(review_client, auth, lead_payload)

    response = review_client.post(
        f"/leads/{lead_id}/drafts/1/approval", json=body, headers=reviewer_headers()
    )

    assert response.status_code == 422
    assert rows(db_path, "approvals") == []


def test_revocation_between_authentication_and_decision_is_refused(
    review_client: TestClient, auth: dict, lead_payload: dict, db_path: Path, token: str
) -> None:
    lead_id = drafted_lead(review_client, auth, lead_payload)
    conn = connect(db_path)
    try:
        reviewer = authenticate(conn, token)
        revoke_reviewer(conn, REVIEWER, clock=lambda: FIXED_NOW)
        with pytest.raises(ReviewerNotActive):
            decide_draft(conn, lead_id, 1, DraftDecisionIn(decision="approved"), reviewer=reviewer)
    finally:
        conn.close()
    assert rows(db_path, "approvals") == []
