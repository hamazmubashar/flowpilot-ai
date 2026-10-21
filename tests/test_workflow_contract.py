"""M3-P2 contract: the API call sequence the first n8n workflow will make.

The planned workflow ("prepare next scored lead") takes the lowest-ID `scored` lead, requests an
advisory qualification and a reply draft with per-lead idempotency keys, and stops there. These
tests replay that sequence against the existing endpoints (LLM disabled) so the workflow can rely
on them; they add no endpoints and change no behaviour.
"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from flowpilot.db.connection import connect
from flowpilot.services.assist import DRAFT_SCOPE, QUALIFY_SCOPE

ACTOR = "n8n:prepare-next-lead"
NON_DRAFTABLE = ["new", "awaiting_approval", "approved", "rejected", "sent", "closed"]


def qualify_key(lead_id: int) -> str:
    return f"n8n:qualify:lead:{lead_id}:v1"


def draft_key(lead_id: int) -> str:
    return f"n8n:draft:lead:{lead_id}:v1"


def workflow_headers(auth: dict, key: str, execution: int) -> dict[str, str]:
    return {
        **auth,
        "Idempotency-Key": key,
        "X-Actor": ACTOR,
        "X-Request-ID": f"n8n-{execution}",
    }


def next_scored(client: TestClient, auth: dict) -> int | None:
    page = client.get("/leads", params={"status": "scored", "limit": 1}, headers=auth)
    assert page.status_code == 200
    body = page.json()
    return body["items"][0]["id"] if body["total"] else None


def qualify(client: TestClient, auth: dict, lead_id: int, execution: int) -> Any:
    return client.post(
        f"/leads/{lead_id}/qualification",
        headers=workflow_headers(auth, qualify_key(lead_id), execution),
    )


def draft(client: TestClient, auth: dict, lead_id: int, execution: int) -> Any:
    return client.post(
        f"/leads/{lead_id}/drafts", headers=workflow_headers(auth, draft_key(lead_id), execution)
    )


def run_workflow(client: TestClient, auth: dict, execution: int = 1) -> int | None:
    """One workflow execution. Returns the prepared lead id, or None when the queue is empty."""
    lead_id = next_scored(client, auth)
    if lead_id is None:
        return None
    assert qualify(client, auth, lead_id, execution).status_code == 201
    assert draft(client, auth, lead_id, execution).status_code == 201
    return lead_id


def create_leads(client: TestClient, auth: dict, payload: dict, count: int) -> list[int]:
    ids = []
    for n in range(count):
        response = client.post(
            "/leads",
            json={
                **payload,
                "company_name": f"Synthetic Workflow Co {n}",
                "contact_email": f"contact{n}@workflow-{n}.example",
            },
            headers=auth,
        )
        assert response.status_code == 201
        ids.append(response.json()["lead"]["id"])
    return ids


def count_rows(db_path: Path, table: str, lead_id: int | None = None) -> int:
    conn = connect(db_path)
    try:
        if lead_id is None:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
        return conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE lead_id = ?",  # noqa: S608
            (lead_id,),
        ).fetchone()[0]
    finally:
        conn.close()


def table_counts(db_path: Path) -> dict[str, int]:
    tables = ("leads", "lead_assessments", "drafts", "llm_runs", "audit_log", "idempotency_keys")
    return {table: count_rows(db_path, table) for table in tables}


def stored_keys(db_path: Path) -> set[tuple[str, str]]:
    conn = connect(db_path)
    try:
        return {(r["scope"], r["key"]) for r in conn.execute("SELECT * FROM idempotency_keys")}
    finally:
        conn.close()


def workflow_audit(client: TestClient, auth: dict, lead_id: int) -> list[tuple[str, str, str]]:
    return [
        (entry["action"], entry["actor"], entry["request_id"])
        for entry in client.get(f"/leads/{lead_id}/audit", headers=auth).json()
        if entry["action"] in {"lead.qualified", "lead.drafted"}
    ]


def lead_status(client: TestClient, auth: dict, lead_id: int) -> str:
    return client.get(f"/leads/{lead_id}", headers=auth).json()["status"]


def set_status(db_path: Path, lead_id: int, status: str) -> None:
    conn = connect(db_path)
    try:
        conn.execute("UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))
    finally:
        conn.close()


# --- selection -------------------------------------------------------------------------------


def test_run_prepares_lowest_id_scored_lead_only(
    client: TestClient, auth: dict, lead_payload: dict, db_path: Path
) -> None:
    first, *others = create_leads(client, auth, lead_payload, 3)

    assert run_workflow(client, auth, execution=41) == first

    assert lead_status(client, auth, first) == "drafted"
    assert count_rows(db_path, "lead_assessments", first) == 1
    drafts = client.get(f"/leads/{first}/drafts", headers=auth).json()
    assert len(drafts) == 1
    assert drafts[0]["generator"] == "template"
    assert drafts[0]["fallback_reason"] == "disabled"
    assert drafts[0]["requires_human_approval"] is True
    assert workflow_audit(client, auth, first) == [
        ("lead.qualified", ACTOR, "n8n-41"),
        ("lead.drafted", ACTOR, "n8n-41"),
    ]

    for lead_id in others:
        assert lead_status(client, auth, lead_id) == "scored"
        assert count_rows(db_path, "lead_assessments", lead_id) == 0
        assert count_rows(db_path, "drafts", lead_id) == 0
        assert count_rows(db_path, "llm_runs", lead_id) == 0


# --- idempotency -----------------------------------------------------------------------------


def test_node_retry_after_lost_response_creates_no_duplicates(
    client: TestClient, auth: dict, lead_payload: dict, db_path: Path
) -> None:
    (lead_id,) = create_leads(client, auth, lead_payload, 1)
    first_qual = qualify(client, auth, lead_id, execution=1)
    first_draft = draft(client, auth, lead_id, execution=1)
    before = table_counts(db_path)

    # Retry-on-fail re-sends a node's request within the same execution, so the request id and
    # idempotency key are unchanged. `drafted` is still draftable, so without the key the draft
    # retry would create a second version; the key makes it a replay instead.
    replay_qual = qualify(client, auth, lead_id, execution=1)
    replay_draft = draft(client, auth, lead_id, execution=1)

    for first, replay in ((first_qual, replay_qual), (first_draft, replay_draft)):
        assert replay.status_code == 201
        assert replay.headers["Idempotent-Replayed"] == "true"
        assert replay.json() == first.json()
    assert table_counts(db_path) == before
    assert lead_status(client, auth, lead_id) == "drafted"


def test_overlapping_executions_on_same_lead_create_no_duplicates(
    client: TestClient, auth: dict, lead_payload: dict, db_path: Path
) -> None:
    """If two executions ever select the same lead, the per-lead keys make the second a replay.

    This models only the API side of an overlap (requests interleaved one at a time); it makes
    no claim about whether n8n allows two executions to run at once.
    """
    (lead_id,) = create_leads(client, auth, lead_payload, 1)
    assert next_scored(client, auth) == lead_id  # execution 1 selects
    assert next_scored(client, auth) == lead_id  # execution 2 selects before 1 drafts

    responses = [
        qualify(client, auth, lead_id, execution=1),
        qualify(client, auth, lead_id, execution=2),
        draft(client, auth, lead_id, execution=1),
        draft(client, auth, lead_id, execution=2),
    ]

    assert [r.status_code for r in responses] == [201] * 4
    assert [r.headers.get("Idempotent-Replayed") for r in responses] == [
        None,
        "true",
        None,
        "true",
    ]
    assert count_rows(db_path, "lead_assessments", lead_id) == 1
    assert count_rows(db_path, "drafts", lead_id) == 1
    assert count_rows(db_path, "llm_runs", lead_id) == 2
    assert workflow_audit(client, auth, lead_id) == [
        ("lead.qualified", ACTOR, "n8n-1"),
        ("lead.drafted", ACTOR, "n8n-1"),
    ]
    assert lead_status(client, auth, lead_id) == "drafted"


def test_run_interrupted_after_qualification_resumes_without_duplicates(
    client: TestClient, auth: dict, lead_payload: dict, db_path: Path
) -> None:
    (lead_id,) = create_leads(client, auth, lead_payload, 1)
    assert qualify(client, auth, lead_id, execution=1).status_code == 201
    # The execution stops before drafting; the lead is still queued.
    assert next_scored(client, auth) == lead_id

    assert run_workflow(client, auth, execution=2) == lead_id

    assert count_rows(db_path, "lead_assessments", lead_id) == 1
    assert count_rows(db_path, "drafts", lead_id) == 1
    assert count_rows(db_path, "llm_runs", lead_id) == 2  # one qualification, one draft
    assert lead_status(client, auth, lead_id) == "drafted"


# --- queue progression -----------------------------------------------------------------------


def test_drafted_lead_leaves_scored_queue(
    client: TestClient, auth: dict, lead_payload: dict
) -> None:
    first, second = create_leads(client, auth, lead_payload, 2)

    assert run_workflow(client, auth, execution=1) == first
    queue = client.get("/leads", params={"status": "scored"}, headers=auth).json()
    assert [item["id"] for item in queue["items"]] == [second]
    assert queue["total"] == 1

    assert run_workflow(client, auth, execution=2) == second
    assert run_workflow(client, auth, execution=3) is None


# --- status rules ----------------------------------------------------------------------------


@pytest.mark.parametrize("status", NON_DRAFTABLE)
def test_invalid_draft_transition_is_409_and_writes_nothing(
    client: TestClient, auth: dict, lead_payload: dict, db_path: Path, status: str
) -> None:
    (lead_id,) = create_leads(client, auth, lead_payload, 1)
    set_status(db_path, lead_id, status)
    before = table_counts(db_path)

    for execution in (1, 2):  # a refused request stores no key, so a retry is refused the same way
        response = draft(client, auth, lead_id, execution=execution)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "lead_not_draftable"
        assert (DRAFT_SCOPE, draft_key(lead_id)) not in stored_keys(db_path)

    assert table_counts(db_path) == before
    assert count_rows(db_path, "drafts", lead_id) == 0
    assert lead_status(client, auth, lead_id) == status


def test_status_change_between_selection_and_draft_keeps_qualification_only(
    client: TestClient, auth: dict, lead_payload: dict, db_path: Path
) -> None:
    """A lead that changes status after qualification keeps its assessment but gets no draft.

    Qualification and drafting are separate requests, so the draft's 409 does not undo the
    advisory assessment already written. The workflow treats that 409 as a skip.
    """
    (lead_id,) = create_leads(client, auth, lead_payload, 1)
    assert next_scored(client, auth) == lead_id

    qual = qualify(client, auth, lead_id, execution=1)
    set_status(db_path, lead_id, "closed")  # e.g. a human closes the lead mid-run
    response = draft(client, auth, lead_id, execution=1)

    assert qual.status_code == 201
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "lead_not_draftable"
    assert count_rows(db_path, "lead_assessments", lead_id) == 1
    assert count_rows(db_path, "drafts", lead_id) == 0
    conn = connect(db_path)
    try:
        tasks = [
            r["task"]
            for r in conn.execute("SELECT task FROM llm_runs WHERE lead_id = ?", (lead_id,))
        ]
    finally:
        conn.close()
    assert tasks == ["qualification"]
    keys = stored_keys(db_path)
    assert (QUALIFY_SCOPE, qualify_key(lead_id)) in keys
    assert (DRAFT_SCOPE, draft_key(lead_id)) not in keys
    assert workflow_audit(client, auth, lead_id) == [("lead.qualified", ACTOR, "n8n-1")]
    assert lead_status(client, auth, lead_id) == "closed"
    assert next_scored(client, auth) is None  # the lead has left the queue


# --- guardrails ------------------------------------------------------------------------------


def test_workflow_creates_no_approval_or_outbox_records(
    client: TestClient, auth: dict, lead_payload: dict, db_path: Path
) -> None:
    ids = create_leads(client, auth, lead_payload, 3)

    prepared = []
    for execution in range(1, 10):
        lead_id = run_workflow(client, auth, execution=execution)
        if lead_id is None:
            break
        prepared.append(lead_id)

    assert prepared == ids
    assert count_rows(db_path, "approvals") == 0
    assert count_rows(db_path, "outbox") == 0
    # The workflow never moves a lead past `drafted`.
    assert {lead_status(client, auth, lead_id) for lead_id in ids} == {"drafted"}
