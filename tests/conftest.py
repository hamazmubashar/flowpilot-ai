from __future__ import annotations

import socket
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from flowpilot.api.app import create_app
from flowpilot.db.connection import connect
from flowpilot.db.migrate import migrate
from flowpilot.settings import Settings

API_KEY = "test-api-key-0123456789abcdefghijklmnop"
FIXED_NOW = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def _block_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that tries to open a non-loopback connection."""
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def check(sock: socket.socket, address: Any) -> None:
        if sock.family == socket.AF_UNIX:
            return
        if address[0] not in LOOPBACK:
            raise RuntimeError(f"external network access blocked in tests: {address!r}")

    def guarded_connect(self: socket.socket, address: Any) -> None:
        check(self, address)
        return real_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> int:
        check(self, address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "flowpilot.db"
    conn = connect(path)
    try:
        migrate(conn)
    finally:
        conn.close()
    return path


@pytest.fixture
def conn(db_path: Path) -> Iterator:
    connection = connect(db_path)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def settings(db_path: Path, tmp_path: Path) -> Settings:
    return Settings(api_key=API_KEY, db_path=db_path, backup_dir=tmp_path / "backups")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings, clock=lambda: FIXED_NOW)) as test_client:
        yield test_client


@pytest.fixture
def auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


@pytest.fixture
def lead_payload() -> dict[str, Any]:
    return {
        "company_name": "Glasswing Logistics",
        "contact_name": "Rowan Hollis",
        "contact_email": "Rowan.Hollis@glasswing-logistics.example",
        "company_size": "51-200",
        "industry": "logistics",
        "service_interest": "process_automation",
        "budget_band": "50k_100k",
        "timeline": "1_3_months",
        "source": "web_form",
        "message": "We re-key every order from email into three systems and want to automate it.",
    }
