import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from virtual_you.backend.app import create_app
from virtual_you.backend.config import Settings

KEY = "local-test-key-with-at-least-24-characters"


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", api_key=KEY, heartbeat_enabled=False)


@pytest.fixture
def record():
    return json.loads((Path(__file__).parents[1] / "fixtures" / "activity_record.json").read_text())


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        client.headers["Authorization"] = "Bearer " + KEY
        yield client


def persona_payload(recipient="manager", formal=True):
    greeting, closing = ("Dear colleague", "Regards") if formal else ("Hey team!", "Cheers")
    return {
        "recipient_id": recipient,
        "display_name": recipient.title(),
        "messages": [
            f"{greeting}, I reviewed the task and shared an update {i}. {closing}."
            for i in range(10)
        ],
    }


def prepare(client, record):
    response = client.post("/api/activities", json=record)
    assert response.status_code == 201, response.text
    response = client.post("/api/personas", json=persona_payload())
    assert response.status_code == 201, response.text


def new_draft(client, **updates):
    body = {
        "recipient_id": "manager",
        "destination": {"platform": "slack", "target": "demo-channel"},
    }
    body.update(updates)
    response = client.post("/api/drafts", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def approve(client, draft):
    response = client.post(
        f"/api/drafts/{draft['id']}/decision",
        json={"expected_revision": draft["revision"], "action": "approve"},
    )
    assert response.status_code == 200, response.text
    return response.json()
