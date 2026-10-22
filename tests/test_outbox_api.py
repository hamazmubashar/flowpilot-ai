"""M5: read-only `GET /outbox`. Lists the simulated outbox written by human approvals (M4).

Reading never writes, audits or sends anything; every entry's channel is "simulated".
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flowpilot.db.connection import connect
from tests.test_review_api import decide, drafted_lead, make_reviewer

TABLES = ("leads", "drafts", "approvals", "outbox", "audit_log", "idempotency_keys", "llm_runs")


def snapshot(db_path: Path) -> dict[str, list[tuple]]:
    conn = connect(db_path)
    try:
        return {
            t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY rowid")]  # noqa: S608
            for t in TABLES
        }
    finally:
        conn.close()


@pytest.fixture
def reviewer_headers(auth: dict, db_path: Path) -> dict:
    return {**auth, "X-Reviewer-Token": make_reviewer(db_path)}


def approved_leads(
    client: TestClient, auth: dict, payload: dict, reviewer_headers: dict, count: int
) -> list[int]:
    ids = []
    for n in range(count):
        lead_id = drafted_lead(
            client,
            auth,
            {**payload, "contact_email": f"p{n}@co{n}.example", "company_name": f"Co {n}"},
        )
        assert decide(client, lead_id, 1, reviewer_headers).status_code == 201
        ids.append(lead_id)
    return ids


def test_requires_the_api_key(client: TestClient, auth: dict) -> None:
    assert client.get("/outbox").status_code == 401
    assert client.get("/outbox", headers={"X-API-Key": "wrong-key-" * 4}).status_code == 401
    assert client.get("/outbox", headers=auth).status_code == 200


def test_empty_outbox(client: TestClient, auth: dict) -> None:
    assert client.get("/outbox", headers=auth).json() == {
        "items": [],
        "total": 0,
        "limit": 25,
        "offset": 0,
    }


def test_approval_appears_once_with_its_lead_draft_and_reviewer(
    client: TestClient, auth: dict, lead_payload: dict, reviewer_headers: dict
) -> None:
    lead_id = drafted_lead(client, auth, lead_payload, drafts=2)
    assert decide(client, lead_id, 2, reviewer_headers).status_code == 201
    drafts = client.get(f"/leads/{lead_id}/drafts", headers=auth).json()

    body = client.get("/outbox", headers=auth).json()
    assert body["total"] == 1
    (entry,) = body["items"]
    assert entry["lead_id"] == lead_id
    assert entry["contact_name"] == lead_payload["contact_name"]
    assert entry["company_name"] == lead_payload["company_name"]
    assert entry["draft_version"] == 2
    assert entry["draft_id"] == drafts[1]["id"]
    assert entry["subject"] == drafts[1]["subject"]
    assert entry["body"] == drafts[1]["body"]
    assert entry["recipient"] == lead_payload["contact_email"].lower()
    assert entry["decided_by"] == "reviewer:avery"
    assert entry["channel"] == "simulated"
    assert entry["status"] == "pending"
    assert entry["sent_at"] is None
    assert "idempotency_key" not in entry


def test_rejection_adds_nothing(
    client: TestClient, auth: dict, lead_payload: dict, reviewer_headers: dict
) -> None:
    lead_id = drafted_lead(client, auth, lead_payload)
    response = decide(client, lead_id, 1, reviewer_headers, decision="rejected")
    assert response.status_code == 201
    assert client.get("/outbox", headers=auth).json()["total"] == 0


def test_newest_first_with_paging(
    client: TestClient, auth: dict, lead_payload: dict, reviewer_headers: dict
) -> None:
    ids = approved_leads(client, auth, lead_payload, reviewer_headers, 3)
    first = client.get("/outbox?limit=2", headers=auth).json()
    second = client.get("/outbox?limit=2&offset=2", headers=auth).json()
    assert (first["total"], first["limit"], first["offset"]) == (3, 2, 0)
    assert [e["lead_id"] for e in first["items"]] == [ids[2], ids[1]]
    assert [e["lead_id"] for e in second["items"]] == [ids[0]]
    assert client.get("/outbox?offset=10", headers=auth).json()["items"] == []


def test_status_filter(
    client: TestClient, auth: dict, lead_payload: dict, reviewer_headers: dict
) -> None:
    approved_leads(client, auth, lead_payload, reviewer_headers, 2)
    assert client.get("/outbox?status=pending", headers=auth).json()["total"] == 2
    assert client.get("/outbox?status=sent", headers=auth).json()["total"] == 0  # never sent
    assert client.get("/outbox?status=failed", headers=auth).json()["total"] == 0


@pytest.mark.parametrize(
    "query", ["status=delivered", "limit=0", "limit=101", "offset=-1", "status=PENDING"]
)
def test_invalid_query_is_rejected(client: TestClient, auth: dict, query: str) -> None:
    assert client.get(f"/outbox?{query}", headers=auth).status_code == 422


def test_reads_do_not_change_the_database(
    client: TestClient, auth: dict, lead_payload: dict, reviewer_headers: dict, db_path: Path
) -> None:
    approved_leads(client, auth, lead_payload, reviewer_headers, 2)
    before = snapshot(db_path)
    for query in ("", "?status=pending", "?limit=1&offset=1", "?status=sent"):
        assert client.get(f"/outbox{query}", headers=auth).status_code == 200
    assert client.get("/outbox?status=bogus", headers=auth).status_code == 422
    assert snapshot(db_path) == before
