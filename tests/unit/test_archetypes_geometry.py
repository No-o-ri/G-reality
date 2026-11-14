import pytest

from greality_contracts import GeometryDefinition
from greality_contracts.geometry import GeometryValidationError, validate_node
from world_api.archetypes import ARCHETYPE_IDS, library


@pytest.mark.parametrize("archetype_id", ARCHETYPE_IDS)
def test_library_archetypes_validate(archetype_id):
    archetype = library()[archetype_id]
    assert GeometryDefinition.model_validate(archetype.model_dump()) == archetype
    assert validate_node(archetype.root.model_dump(), set(archetype.parameters), {m.id for m in archetype.materials}) > 0


def sphere():
    return {"op": "sphere", "params": {"radius": 1}}


@pytest.mark.parametrize("node,message", [
    ({"op": "execute"}, "unknown op"),
    ({"op": "sphere", "params": {"radius": -1}}, "below minimum"),
    ({"op": "sphere", "params": {"radius": 201}}, "above maximum"),
    ({"op": "sphere", "params": {"radius": {"ref": "missing"}}}, "unknown parameter"),
    ({"op": "union", "children": [sphere()]}, "takes 2..8 children"),
    ({**sphere(), "material": "missing"}, "unknown material"),
])
def test_rejects_invalid_nodes(node, message):
    with pytest.raises(GeometryValidationError, match=message):
        validate_node(node, set(), {"stone"})


def test_rejects_too_deep_tree():
    node = sphere()
    for _ in range(11):
        node = {"op": "translate", "params": {"offset": [0, 0, 0]}, "children": [node]}
    assert validate_node(node, set(), set()) == 12
    node = {"op": "translate", "params": {"offset": [0, 0, 0]}, "children": [node]}
    with pytest.raises(GeometryValidationError, match="tree deeper than 12"):
        validate_node(node, set(), set())


def test_rejects_too_many_nodes_independently_of_depth_and_arity():
    def tree(depth):
        return sphere() if depth == 0 else {"op": "union", "children": [tree(depth - 1), tree(depth - 1)]}
    assert validate_node(tree(6), set(), set()) == 127
    with pytest.raises(GeometryValidationError, match="more than 128 nodes"):
        validate_node(tree(7), set(), set())
