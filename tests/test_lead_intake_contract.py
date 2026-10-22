"""The API contract `n8n/workflows/flowpilot-lead-intake.json` relies on.

n8n cannot run in the test suite, so this replays what the workflow sends (the caller's JSON
body, `Idempotency-Key: n8n:intake:<key>`, `X-Actor: n8n:lead-intake`) against the real app on a
temporary database, using the Step B demo scenarios, and checks the status codes the workflow
branches on and the response fields its Respond nodes read.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from flowpilot.db.connection import connect
from flowpilot.demo import SCENARIOS

HEADERS = {"X-Actor": "n8n:lead-intake"}


def submit(client: TestClient, auth: dict, key: str, body: dict):
    headers = {**auth, **HEADERS, "Idempotency-Key": f"n8n:intake:{key}"}
    return client.post("/leads", json=body, headers=headers)


def by_key(key: str):
    return next(s for s in SCENARIOS if s.key == key)


def test_branches_and_fields_the_workflow_reads(client: TestClient, auth: dict) -> None:
    created = submit(client, auth, "demo-hot", by_key("hot").payload)
    assert created.status_code == 201  # "Created?" branch
    body = created.json()
    lead = body["lead"]
    # Respond: created reads these exact fields.
    assert (lead["id"], lead["score"]["tier"], lead["score"]["route"]) == (
        1,
        "hot",
        "partner_review",
    )
    assert lead["score"]["total"] == 98
    assert body["replayed"] is False

    duplicate = submit(client, auth, "demo-dup", by_key("duplicate").payload)
    assert duplicate.status_code == 200  # "Duplicate?" branch
    assert duplicate.json()["lead"]["id"] == 1
    assert duplicate.json()["duplicate"] is True

    invalid = submit(client, auth, "demo-bad", by_key("invalid").payload)
    assert invalid.status_code == 422  # "Invalid?" branch
    assert any(e["loc"][-1] == "contact_email" for e in invalid.json()["detail"])

    unknown_field = submit(client, auth, "demo-extra", {**by_key("warm").payload, "x": 1})
    assert unknown_field.status_code == 422  # the API, not n8n, rejects extra fields


def test_a_retried_webhook_call_replays_instead_of_duplicating(
    client: TestClient, auth: dict, db_path: Path
) -> None:
    first = submit(client, auth, "client-retry-1", by_key("warm").payload)
    again = submit(client, auth, "client-retry-1", by_key("warm").payload)
    assert (first.status_code, again.status_code) == (201, 201)
    assert again.json()["replayed"] is True
    assert again.json()["lead"]["id"] == first.json()["lead"]["id"]

    reused = submit(client, auth, "client-retry-1", by_key("cold").payload)
    assert reused.status_code == 422  # same key, different body: refused, nothing stored
    assert reused.json()["detail"]["code"] == "idempotency_key_reused"

    conn = connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1
        actors = {r[0] for r in conn.execute("SELECT DISTINCT actor FROM audit_log")}
    finally:
        conn.close()
    assert actors == {"n8n:lead-intake"}
