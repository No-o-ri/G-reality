"""HTTP surface: auth, origin checks and one end-to-end command through FastAPI against Neon."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from world_api.app import create_app
from world_api.regions import region_of

pytestmark = pytest.mark.neon
TOKEN = "test-token"


@pytest.fixture()
def client(service, settings):
    with TestClient(create_app(settings, service=service, token=TOKEN)) as c:
        yield c


def auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def test_health_is_open_and_reports_database(client):
    r = client.get("/v1/health")
    assert r.status_code == 200 and r.json()["database"] == "connected"
    assert "token" not in r.text.lower()


def test_auth_and_origin_are_enforced(client, world):
    assert client.get(f"/v1/worlds/{world.world_id}").status_code == 401
    assert client.get(f"/v1/worlds/{world.world_id}", headers={"Authorization": "Bearer wrong"}).status_code == 401
    evil = client.get(f"/v1/worlds/{world.world_id}", headers={**auth(), "Origin": "https://evil.example"})
    assert evil.status_code == 403 and evil.json()["code"] == "origin_not_allowed"
    ok = client.get(f"/v1/worlds/{world.world_id}", headers={**auth(), "Origin": "http://localhost:5173"})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert client.get("/v1/worlds/nope", headers=auth()).status_code == 404


def test_command_round_trip_over_http(client, world):
    here = region_of(world.player.position[0], world.player.position[2])
    coords = [{"rx": here.rx + dx, "rz": here.rz + dz} for dx in (-1, 0, 1) for dz in (-1, 0, 1)]
    snaps = client.post(f"/v1/worlds/{world.world_id}/regions:batch", json={"coords": coords}, headers=auth()).json()
    tree = next(e for s in snaps for e in s["entities"] if e["kind"] == "tree")
    p = tree["transform"]["position"]
    body = {"command_id": str(uuid.uuid4()), "world_id": world.world_id, "player_id": "p1",
            "text": "Set this tree on fire", "target_hint": tree["id"],
            "player_position": [p[0] + 2, p[1] + 1.7, p[2]], "view_direction": [-1, 0, 0]}
    r = client.post(f"/v1/worlds/{world.world_id}/commands", json=body, headers=auth())
    assert r.status_code == 200 and r.json()["status"] == "committed"
    bad = client.post(f"/v1/worlds/{world.world_id}/commands", json={**body, "text": ""}, headers=auth())
    assert bad.status_code == 422
    events = client.get(f"/v1/worlds/{world.world_id}/events?after_revision=1", headers=auth()).json()
    assert [e["type"] for e in events] == ["entity_ignited"]
    assert client.get(f"/v1/worlds/{world.world_id}", headers=auth()).json()["revision"] == 2
