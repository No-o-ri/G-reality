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


def test_published_asset_is_served_with_integrity_check(service, settings, world, tmp_path):
    import hashlib

    import numpy as np
    from greality_contracts import Aabb, JobState, NeuralMetrics

    from gpu.ply import GaussianArrays, decode_ply, encode_ply
    from world_api.assets import LocalAssetStore, asset_key

    store = LocalAssetStore(tmp_path)
    coord = region_of(world.player.position[0], world.player.position[2])
    service.load_regions(world.world_id, [coord])
    job = service.enqueue_job(world.world_id, coord, reason="manual")
    assert service.set_job_state(job.job_id, JobState.validating)
    n = 5
    g = GaussianArrays(means=np.zeros((n, 3), np.float32), log_scales=np.full((n, 3), -2, np.float32),
                       quats=np.tile(np.array([1, 0, 0, 0], np.float32), (n, 1)),
                       opacity_logits=np.zeros(n, np.float32), sh_dc=np.zeros((n, 3), np.float32))
    data = encode_ply(g)
    sha = hashlib.sha256(data).hexdigest()
    key = asset_key(world.world_id, coord.rx, coord.rz, job.source_region_revision, sha)
    store.put(key, data)
    status, manifest = service.publish_asset(
        job.job_id, uri=key, sha256=sha, byte_size=len(data), gaussian_count=n, fmt="ply", sh_degree=0,
        origin=(coord.rx * 64.0, 0.0, coord.rz * 64.0), bounds=Aabb(min=(0, 0, 0), max=(1, 1, 1)),
        metrics=NeuralMetrics(rasterizer="torch", iterations=1, gaussian_count=n, capture_seconds=0, train_seconds=0))
    assert status == "published"
    with TestClient(create_app(settings, service=service, token=TOKEN, store=store)) as c:
        assert c.get(f"/v1/assets/{manifest.asset_id}").status_code == 401
        r = c.get(f"/v1/assets/{manifest.asset_id}", headers=auth())
        assert r.status_code == 200 and r.content == data
        assert decode_ply(r.content).count == n
        snap = c.post(f"/v1/worlds/{world.world_id}/regions:batch", headers=auth(),
                      json={"coords": [{"rx": coord.rx, "rz": coord.rz}]}).json()[0]
        assert snap["neural_asset"]["asset_id"] == manifest.asset_id
        assert snap["neural_asset"]["region_revision"] == snap["region"]["revision"]
        store.path(key).write_bytes(data[:-4])  # truncated file is refused, never served
        bad = c.get(f"/v1/assets/{manifest.asset_id}", headers=auth())
        assert bad.status_code == 500 and bad.json()["detail"]["code"] == "asset_corrupt"
        assert c.get("/v1/assets/asset-missing", headers=auth()).status_code == 404
