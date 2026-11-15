"""In-world creation against real Neon (dev branch): a creation is a persisted world mutation that leaves everything else
untouched and survives a new service instance (companion restart), reloads, retries and replay.

The language model is replaced by a fixed CreationDraft, so the test is deterministic and costs no API calls.
"""

from __future__ import annotations

import json
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from greality_contracts import RegionCoord, WorldCommand
from world_api.interpreter import RulesInterpreter
from world_api.regions import region_of
from world_api.service import MAX_CREATED_ARCHETYPES, WorldService
from world_api.worldspec import CreationDraft, CreationObjectDraft, MaterialDraft

pytestmark = pytest.mark.neon

RINGS = {"op": "translate", "params": {"offset": [0, 12, 0]}, "children": [{"op": "union", "children": [
    {"op": "rotate", "params": {"quat": [0.7071, 0, 0, 0.7071]}, "children": [
        {"op": "torus", "params": {"major": 10, "minor": 1}, "material": "glow"}]},
    {"op": "rotate", "params": {"quat": [0, 0, 0.7071, 0.7071]}, "children": [
        {"op": "torus", "params": {"major": 6, "minor": 0.5}}]},
    {"op": "rotate", "params": {"quat": [0.5, 0.5, 0.5, 0.5]}, "children": [
        {"op": "torus", "params": {"major": 6, "minor": 0.5}}]}]}]}


class FakeCreator(RulesInterpreter):
    def __init__(self, objects=None):
        self.calls = 0
        self.objects = objects

    def create(self, text, context):
        self.calls += 1
        return CreationDraft(title="Luminous ring", requested_structures=["toroidal ring"], objects=self.objects or [
            CreationObjectDraft(name="toroidal ring", kind="ring", geometry_json=json.dumps(RINGS), height_m=4.0,
                                materials=[MaterialDraft(id="glow", base_color=[0.6, 0.9, 1.0],
                                                         emissive_color=[0.5, 0.9, 1.0], emissive_strength=3.0)])])


def fresh(settings):
    return psycopg.connect(settings.database_url, row_factory=dict_row, autocommit=True)


def entity_rows(settings, world_id):
    with fresh(settings) as conn:
        return {r["entity_id"]: r for r in conn.execute("SELECT * FROM entities WHERE world_id=%s", (world_id,))}


def build(world, text="Create a gigantic luminous toroidal ring with three smaller intersecting rings", cid=None):
    p = world.player.position
    return WorldCommand(command_id=cid or str(uuid.uuid4()), world_id=world.world_id, player_id="p1", text=text,
                        player_position=(p[0], p[1], p[2]), view_direction=(1.0, 0.0, 0.0), action="build")


@pytest.fixture()
def creator_world(pool):
    creator = FakeCreator()
    svc = WorldService(pool, creator)
    w = svc.create_world("a dense pine forest on rolling hills", seed=4321, world_id=f"test-{uuid.uuid4().hex[:10]}",
                         offline=True)
    yield svc, creator, w
    svc.delete_world(w.world_id)


def test_creation_is_persisted_and_leaves_the_world_intact(creator_world, settings, pool):
    svc, creator, world = creator_world
    here = region_of(world.player.position[0], world.player.position[2])
    coords = [RegionCoord(rx=here.rx + dx, rz=here.rz + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)]
    svc.load_regions(world.world_id, coords)
    before = entity_rows(settings, world.world_id)
    assert before, "test world should have generated entities"

    result = svc.handle_command(build(world))
    assert result.status == "committed", result.message
    assert result.world_revision == world.revision + 1 and len(result.entities) == 1
    created = result.entities[0]
    assert created.origin == "player" and created.geometry_id.startswith("cr")
    assert created.transform.position[0] > world.player.position[0] + 4  # ahead (view +X)
    event = result.events[0]
    assert event.type.value == "entity_built" and event.payload["archetypes"][0]["id"] == created.geometry_id

    after = entity_rows(settings, world.world_id)
    assert set(after) == set(before) | {created.id}
    for eid, row in before.items():  # nothing that existed was moved or changed
        assert after[eid] == row

    # a new service instance = companion restart; everything comes back from Neon exactly
    restarted = WorldService(pool, RulesInterpreter())
    reopened = restarted.get_world(world.world_id)
    stored = next(g for g in reopened.spec.archetypes if g.id == created.geometry_id)
    assert stored.model_dump(mode="json") == event.payload["archetypes"][0]
    assert reopened.revision == result.world_revision
    snap = restarted.load_regions(world.world_id, [created.region])[0]
    again = next(e for e in snap.entities if e.id == created.id)
    assert again.transform == created.transform and again.geometry_params == created.geometry_params
    with fresh(settings) as conn:
        defs = conn.execute("SELECT definition FROM geometry_definitions WHERE world_id=%s AND geometry_id=%s",
                            (world.world_id, created.geometry_id)).fetchall()
    assert len(defs) == 1 and defs[0]["definition"]["root"] == stored.root.model_dump(mode="json")
    assert restarted.replay(world.world_id)[created.id].transform == created.transform

    # retrying the same command returns the stored result without asking the model or writing again
    retry = svc.handle_command(build(world, cid=result.command_id))
    assert retry.model_dump() == result.model_dump() and creator.calls == 1
    assert set(entity_rows(settings, world.world_id)) == set(after)


def test_creation_budget_and_invalid_geometry_are_rejected_without_writes(pool, settings):
    bad = FakeCreator([CreationObjectDraft(name="nothing", geometry_json='{"op": "teapot"}')])
    svc = WorldService(pool, bad)
    world = svc.create_world("rolling hills", seed=99, world_id=f"test-{uuid.uuid4().hex[:10]}", offline=True)
    try:
        result = svc.handle_command(build(world))
        assert result.status == "rejected" and "Nothing could be built" in result.message
        assert svc.get_world(world.world_id).revision == world.revision
        assert not [e for e in entity_rows(settings, world.world_id).values() if e["origin"] == "player"]

        svc.interpreter = FakeCreator()
        for _ in range(MAX_CREATED_ARCHETYPES):
            assert svc.handle_command(build(world)).status == "committed"
        over = svc.handle_command(build(world))
        assert over.status == "rejected" and over.failures[0].code == "insufficient_resources"
        assert len([g for g in svc.get_world(world.world_id).spec.archetypes if g.id.startswith("cr")]) \
            == MAX_CREATED_ARCHETYPES
    finally:
        svc.delete_world(world.world_id)
