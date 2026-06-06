from __future__ import annotations

from pathlib import Path
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings


@pytest.fixture
def make_client(tmp_path: Path):
    clients: list[TestClient] = []
    exits: list[tuple[TestClient, tuple[Any, Any, Any]]] = []

    def _make(db_name: str = "cockpit.db") -> tuple[TestClient, Settings]:
        settings = Settings(
            db_path=tmp_path / db_name,
            workspace_root=tmp_path,
            openai_api_key=None,
            openai_model="gpt-4.1-mini",
            provider_preference="mock",
        )
        app = create_app(settings)
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        exits.append((client, (None, None, None)))
        return client, settings

    yield _make

    for client, exc in exits:
        client.__exit__(*exc)
    for client in clients:
        client.close()


def wait_for_status(client: TestClient, run_id: int, expected: set[str], tries: int = 50) -> dict:
    latest = {}
    for _ in range(tries):
        latest = client.get(f"/runs/{run_id}").json()
        if latest.get("status") in expected:
            return latest
        time.sleep(0.03)
    return latest
