from typing import Any

import pytest
from fastapi.testclient import TestClient


def post(client: TestClient, auth: dict, payload: dict, key: str | None = None, **headers: str):
    h = {**auth, **headers}
    if key is not None:
        h["Idempotency-Key"] = key
    return client.post("/leads", json=payload, headers=h)


def test_create_lead_scores_and_routes(client: TestClient, auth: dict, lead_payload: dict) -> None:
    response = post(client, auth, lead_payload)
    assert response.status_code == 201
    body = response.json()
    assert body["duplicate"] is False
    assert body["replayed"] is False
    lead = body["lead"]
    assert lead["status"] == "scored"
    assert lead["contact_email"] == "rowan.hollis@glasswing-logistics.example"
    assert lead["created_at"] == "2026-10-20T12:00:00+00:00"
    assert lead["score"]["total"] == 90
    assert lead["score"]["tier"] == "hot"
    assert lead["score"]["route"] == "partner_review"
    assert len(lead["score"]["breakdown"]) == 5


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("contact_email", "real.person@gmail.com"),
        ("industry", "crypto"),
        ("message", "short"),
    ],
)
def test_invalid_payload_is_422(
    client: TestClient, auth: dict, lead_payload: dict, field: str, value: str
) -> None:
    response = post(client, auth, {**lead_payload, field: value})
    assert response.status_code == 422
    assert client.get("/leads", headers=auth).json()["total"] == 0


def test_extra_field_is_422(client: TestClient, auth: dict, lead_payload: dict) -> None:
    assert post(client, auth, {**lead_payload, "ssn": "000-00-0000"}).status_code == 422


def test_idempotent_replay_returns_original(
    client: TestClient, auth: dict, lead_payload: dict
) -> None:
    first = post(client, auth, lead_payload, key="n8n-exec-1")
    second = post(client, auth, lead_payload, key="n8n-exec-1")
    assert first.status_code == second.status_code == 201
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json()["replayed"] is True
    assert second.json()["lead"]["id"] == first.json()["lead"]["id"]
    assert client.get("/leads", headers=auth).json()["total"] == 1
    lead_id = first.json()["lead"]["id"]
    actions = [e["action"] for e in client.get(f"/leads/{lead_id}/audit", headers=auth).json()]
    assert actions == ["lead.created", "lead.scored"]  # replay writes nothing


def test_idempotency_key_reuse_with_different_body_is_422(
    client: TestClient, auth: dict, lead_payload: dict
) -> None:
    post(client, auth, lead_payload, key="k-1")
    response = post(client, auth, {**lead_payload, "budget_band": "under_10k"}, key="k-1")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "idempotency_key_reused"


def test_invalid_idempotency_key_is_422(client: TestClient, auth: dict, lead_payload: dict) -> None:
    assert post(client, auth, lead_payload, key="has spaces").status_code == 422


def test_duplicate_without_key_returns_existing(
    client: TestClient, auth: dict, lead_payload: dict
) -> None:
    first = post(client, auth, lead_payload).json()["lead"]
    variant = {
        **lead_payload,
        "company_name": "GLASSWING LOGISTICS, Inc.",
        "contact_email": "ROWAN.HOLLIS@glasswing-logistics.example",
        "message": "Following up on my earlier enquiry about automation.",
    }
    response = post(client, auth, variant)
    assert response.status_code == 200
    assert response.json()["duplicate"] is True
    assert response.json()["lead"]["id"] == first["id"]
    assert client.get("/leads", headers=auth).json()["total"] == 1
    actions = [e["action"] for e in client.get(f"/leads/{first['id']}/audit", headers=auth).json()]
    assert actions == ["lead.created", "lead.scored", "lead.duplicate_submission"]


def test_duplicate_with_new_key_is_replayed_as_duplicate(
    client: TestClient, auth: dict, lead_payload: dict
) -> None:
    post(client, auth, lead_payload, key="a")
    dup = post(client, auth, lead_payload, key="b")
    assert dup.status_code == 200
    again = post(client, auth, lead_payload, key="b")
    assert again.status_code == 200
    assert again.json() == {**dup.json(), "replayed": True}


def test_audit_records_actor_request_id_and_changes(
    client: TestClient, auth: dict, lead_payload: dict
) -> None:
    lead_id = post(
        client, auth, lead_payload, **{"X-Actor": "n8n:intake", "X-Request-ID": "req-123"}
    ).json()["lead"]["id"]
    entries = client.get(f"/leads/{lead_id}/audit", headers=auth).json()
    assert [e["actor"] for e in entries] == ["n8n:intake", "n8n:intake"]
    assert {e["request_id"] for e in entries} == {"req-123"}
    created, scored = entries
    assert created["before"] is None
    assert created["after"]["company_name"] == "Glasswing Logistics"
    assert scored["before"] == {"status": "new"}
    assert scored["after"]["status"] == "scored"
    assert scored["after"]["tier"] == "hot"


def test_default_actor(client: TestClient, auth: dict, lead_payload: dict) -> None:
    lead_id = post(client, auth, lead_payload).json()["lead"]["id"]
    assert client.get(f"/leads/{lead_id}/audit", headers=auth).json()[0]["actor"] == "api-client"


def test_invalid_actor_header_is_422(client: TestClient, auth: dict, lead_payload: dict) -> None:
    assert post(client, auth, lead_payload, **{"X-Actor": "drop table;"}).status_code == 422


def _seed(client: TestClient, auth: dict, base: dict[str, Any]) -> None:
    variants = [
        {"company_name": "Alpha Co One", "budget_band": "over_100k", "timeline": "immediate"},
        {"company_name": "Beta Two", "budget_band": "under_10k", "timeline": "3_6_months"},
        {
            "company_name": "Gamma Three",
            "budget_band": "under_10k",
            "timeline": "exploring",
            "company_size": "1-10",
            "industry": "other",
            "service_interest": "other",
        },
    ]
    for v in variants:
        assert post(client, auth, {**base, **v}).status_code == 201


def test_list_filters_and_pagination(client: TestClient, auth: dict, lead_payload: dict) -> None:
    _seed(client, auth, lead_payload)
    page = client.get("/leads", headers=auth).json()
    assert page["total"] == 3
    assert [i["score"]["tier"] for i in page["items"]] == ["hot", "warm", "cold"]

    hot = client.get("/leads", params={"tier": "hot"}, headers=auth).json()
    assert [i["company_name"] for i in hot["items"]] == ["Alpha Co One"]

    scored = client.get("/leads", params={"status": "scored"}, headers=auth).json()
    assert scored["total"] == 3
    assert client.get("/leads", params={"status": "new"}, headers=auth).json()["total"] == 0

    paged = client.get("/leads", params={"limit": 2, "offset": 2}, headers=auth).json()
    assert paged["total"] == 3
    assert [i["company_name"] for i in paged["items"]] == ["Gamma Three"]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"tier": "x"}])
def test_list_rejects_bad_params(client: TestClient, auth: dict, params: dict) -> None:
    assert client.get("/leads", params=params, headers=auth).status_code == 422


def test_get_lead_and_404s(client: TestClient, auth: dict, lead_payload: dict) -> None:
    lead_id = post(client, auth, lead_payload).json()["lead"]["id"]
    detail = client.get(f"/leads/{lead_id}", headers=auth).json()
    assert detail["score"]["rules_version"] == "2026.10-v1"
    for path in ("/leads/999", "/leads/999/audit"):
        response = client.get(path, headers=auth)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "lead_not_found"
