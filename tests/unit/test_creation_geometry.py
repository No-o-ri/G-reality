"""Geometric fidelity checks (bounds, lint), new ops in the normalizer, and the layout of in-world creations."""

from __future__ import annotations

import json
import math

import pytest

from greality_contracts import WorldCommand
from world_api.geometry_bounds import bounds, radius_from_origin
from world_api.geometry_normalize import normalize_graph
from world_api.interpreter import RulesInterpreter
from world_api.interpreter.base import CommandContext, InterpreterError
from world_api.service import _lay_out
from world_api.worldspec import (
    CreationDraft,
    CreationObjectDraft,
    MaterialDraft,
    ObjectDraft,
    ParamDraft,
    WorldDraft,
    build_spec,
    draft_report,
    object_behavior,
    object_geometry,
)

FLAT_ARCH = {"op": "intersect", "children": [
    {"op": "torus", "params": {"major": 8.0, "minor": 1.0}},
    {"op": "translate", "params": {"offset": [0, -4, 0]}, "children": [{"op": "box", "params": {"half": [10, 5, 10]}}]}]}
UPRIGHT_RING = {"op": "translate", "params": {"offset": [0, 12, 0]}, "children": [
    {"op": "rotate", "params": {"quat": [0.7071, 0, 0, 0.7071]}, "children": [
        {"op": "torus", "params": {"major": 10, "minor": 1}}]}]}


def obj(name, root, kind="object", params=(), cls=ObjectDraft, **kw):
    return cls(name=name, kind=kind, geometry_json=json.dumps(root),
               materials=[MaterialDraft(id="m", base_color=[0.5, 0.5, 0.9])],
               parameters=[ParamDraft(name=n, min=a, max=b) for n, a, b in params], **kw)


def test_bounds_follow_rotation_and_translation():
    (lo, hi) = bounds(UPRIGHT_RING)
    assert hi[1] == pytest.approx(23, abs=0.05) and lo[1] == pytest.approx(1, abs=0.05)  # stands up, floats 1 m
    assert hi[2] == pytest.approx(1, abs=0.05)  # thin along Z after the rotation
    (lo, hi) = bounds(FLAT_ARCH)
    assert lo[1] == pytest.approx(-1) and hi[1] == pytest.approx(1)  # the dreamscape 'arch' lay flat, half buried


def test_bounds_of_new_ops_and_refs():
    spiral = {"op": "spiral_repeat", "params": {"count": 10, "pitch": 5, "copies": 31},
              "children": [{"op": "translate", "params": {"offset": [4, 0, 0]},
                            "children": [{"op": "box", "params": {"half": [1, 0.2, 0.5]}}]}]}
    (lo, hi) = bounds(spiral)
    assert hi[1] == pytest.approx(0.2 + 30 * 0.5) and hi[0] == pytest.approx(math.hypot(5, 0.5))
    (lo, hi) = bounds({"op": "polyhedron", "params": {"kind": 1, "radius": 2}})
    assert hi[0] == pytest.approx(2 * math.sqrt(3))
    ref = {"op": "sphere", "params": {"radius": {"ref": "r"}}}
    assert bounds(ref, {"r": {"min": 1, "max": 4, "default": 2}})[1][0] == 4  # refs take their maximum
    assert bounds({"op": "plane", "params": {"normal": [0, 1, 0], "offset": 0}}) is None


def test_normalizer_maps_named_solids_and_helix():
    assert normalize_graph({"op": "Dodecahedron", "params": {"size": 3}}) == {
        "op": "polyhedron", "params": {"kind": 3, "radius": 3.0}, "children": []}
    n = normalize_graph({"op": "helix", "params": {"count": 8, "pitch": 3, "copies": 20}, "children": [{"op": "cube"}]})
    assert n["op"] == "spiral_repeat" and n["children"][0]["op"] == "box"


def test_bound_radius_is_measured_not_trusted():
    g = object_geometry(obj("floating ring", UPRIGHT_RING, size_m=2.0), 0)
    assert g.bound_radius >= radius_from_origin(bounds(UPRIGHT_RING))
    with pytest.raises(ValueError, match="unbounded"):
        object_geometry(obj("floor", {"op": "plane", "params": {"normal": [0, 1, 0], "offset": 0}}), 0)


def test_report_flags_the_dreamscape_failures():
    pylon = {"op": "smooth_union", "params": {"k": 0.5}, "children": [
        {"op": "cone", "params": {"half_height": 3, "radius_bottom": 1.5, "radius_top": 0.2}},
        {"op": "translate", "params": {"offset": [0, 2, 0]}, "children": [
            {"op": "superquadric", "params": {"radii": [1.2, 1.2, 1.2], "exponent": 0.6}}]}]}
    objects = [obj("Crystalline Arch", FLAT_ARCH, "arch", params=[("scale_factor", 0.8, 1.5)]),
               obj("Alien Fractal Organism", pylon, "organism", params=[("p_height", 2, 5)])]
    hard, every = draft_report(objects, ["iridescent ring", "spiral tower", "crystalline arch", "levitating polyhedron"])
    text = "\n".join(hard)
    assert "never uses them" in text and "buried" in text
    assert "'iridescent ring' has no object" in text and "'spiral tower' has no object" in text
    assert "'levitating polyhedron' has no object" in text and "'crystalline arch'" not in text
    assert any("it is flat" in line for line in every)  # soft hint, only sent along with a repair


def test_report_accepts_a_recognizable_upright_ring():
    hard, _ = draft_report([obj("floating ring", {"op": "union", "children": [UPRIGHT_RING, {
        "op": "translate", "params": {"offset": [0, 12, 0]}, "children": [{"op": "rotate", "params": {
            "quat": [0, 0, 0.7071, 0.7071]}, "children": [{"op": "torus", "params": {"major": 7, "minor": 0.6}}]}]}]})],
        ["floating rings"])
    assert hard == []


def test_build_spec_bound_radius_contains_floating_geometry():
    draft = WorldDraft(title="t", relief_m=20, objects=[obj("floating ring", UPRIGHT_RING, count=3, size_m=3)])
    spec = build_spec(draft, "rings", 7)
    assert spec.archetypes[0].bound_radius >= 23


def test_rules_interpreter_refuses_to_design_geometry():
    with pytest.raises(InterpreterError) as err:
        RulesInterpreter().create("build a tower", CommandContext())
    assert "Gemini" in err.value.message


def _layout(draft, view=(0.0, 0.0, -1.0), nearby=()):
    spec = build_spec(WorldDraft(title="flat", relief_m=0.0), "flat", 3)
    built = []
    for i, o in enumerate(draft.objects):
        g = object_geometry(o, i, id_prefix="crtest")
        built.append((o, g, object_behavior(o, g, [])))
    cmd = WorldCommand(command_id="c" * 12, world_id="w-test", player_id="p1", text="x",
                       player_position=(0.0, 1.7, 0.0), view_direction=view, action="build")
    return _lay_out(cmd, draft, built, spec, 3, list(nearby), "abcd1234"), spec


def test_layout_places_objects_ahead_clear_of_the_player_facing_them():
    ring = obj("ring", UPRIGHT_RING, cls=CreationObjectDraft, height_m=5.0, scale=2.0)
    tower = obj("tower", {"op": "translate", "params": {"offset": [0, 10, 0]}, "children": [
        {"op": "cylinder", "params": {"half_height": 10, "radius": 2}}]}, cls=CreationObjectDraft, right_m=15.0)
    (e_ring, e_tower), spec = _layout(CreationDraft(title="gate", objects=[ring, tower]))
    for e in (e_ring, e_tower):
        assert e.transform.position[2] < -4.0  # ahead of the player (view -Z), footprint clear of the player
        assert e.origin == "player" and e.geometry_id.startswith("crtest")
    foot = 11 * 2.0  # ring footprint radius at scale 2
    assert -e_ring.transform.position[2] >= 4.0 + foot - 1e-6
    assert e_tower.transform.position[0] == pytest.approx(15.0)  # right of a player looking along -Z is +X
    ground = 0.0  # relief 0
    assert e_ring.transform.position[1] == pytest.approx(ground + 5.0, abs=0.6)
    q = e_ring.transform.rotation  # local +Z turned towards the player (+Z here): identity yaw
    assert abs(q[1]) == pytest.approx(0.0, abs=1e-6) and abs(q[3]) == pytest.approx(1.0)
    measured = radius_from_origin(bounds(UPRIGHT_RING)) * 1.05
    assert e_ring.bound_radius == pytest.approx(measured * 2.0)  # archetype bound (measured) times the entity scale


def test_layout_scale_respects_the_entity_bound_limit():
    huge = obj("huge ring", {"op": "translate", "params": {"offset": [0, 60, 0]}, "children": [
        {"op": "rotate", "params": {"quat": [0.7071, 0, 0, 0.7071]}, "children": [
            {"op": "torus", "params": {"major": 60, "minor": 3}}]}]}, cls=CreationObjectDraft, scale=5.0)
    (e,), _ = _layout(CreationDraft(title="huge", objects=[huge]))
    assert e.bound_radius <= 200.0
