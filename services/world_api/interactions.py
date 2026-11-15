"""Pure execution of the closed, declarative generated-interaction grammar."""

from __future__ import annotations

import operator
import re

from greality_contracts import (
    Capability, Condition, EventType, InteractionSpec, ObjectBehavior, PlayerState, WorldEntity,
)

from world_api.actions import Effect
from world_api.simulation import ignite


class InteractionError(ValueError):
    """An interaction's guards or effect arguments prevent execution."""


_NAME = re.compile(r"[a-z0-9_]{1,24}")
_COMPARE = {"<": operator.lt, "<=": operator.le, ">": operator.gt,
            ">=": operator.ge, "==": operator.eq, "!=": operator.ne}


def _name(value: str | None) -> str:
    if value is None or _NAME.fullmatch(value) is None:
        raise InteractionError(f"Invalid variable or resource name: {value!r}")
    return value


def check(spec: InteractionSpec, entity: WorldEntity) -> list[str]:
    """Return human-readable failures, using zero for absent state variables."""
    failures = []
    for requirement in spec.requires:
        if requirement.condition is not None:
            op = "!=" if requirement.op == "!=" else "=="
            actual = entity.state.condition
            expected = requirement.condition
            label = "condition"
        else:
            label = _name(requirement.var)
            actual = entity.state.vars.get(label, 0.0)
            expected = requirement.value
            op = requirement.op
        compare = _COMPARE.get(op)
        if compare is None:
            raise InteractionError(f"Unknown comparison operator: {op!r}")
        if not compare(actual, expected):
            value = actual.value if isinstance(actual, Condition) else actual
            failures.append(f"Requires {label} {op} {expected}; currently {value}.")
    return failures


def available(behavior: ObjectBehavior | None, entity: WorldEntity) -> list[InteractionSpec]:
    """Return allowed player interactions in their authored order."""
    if behavior is None:
        return []
    return [spec for spec in behavior.interactions if spec.player_initiated and not check(spec, entity)]


def perform(spec: InteractionSpec, entity: WorldEntity, behaviors: dict[str, ObjectBehavior],
            neighbours: list[WorldEntity], player: PlayerState, tick: int, seed: int
            ) -> tuple[list[Effect], PlayerState | None, list[dict]]:
    """Execute on private copies, with at most one level of nearby interactions.

    Nearby candidate slots are selected by distance and ID before checking their
    behavior and guards. Repeated triggers observe the latest target state.
    """
    failures = check(spec, entity)
    if failures:
        raise InteractionError(" ".join(failures))

    originals = {target.id: target for target in neighbours if target.id != entity.id}
    originals[entity.id] = entity
    entities = {key: target.model_copy(deep=True) for key, target in originals.items()}
    new_player = None
    emits: list[dict] = []
    triggered: dict[str, str] = {}
    touched: dict[str, None] = {}

    def apply(interaction: InteractionSpec, target_id: str, nearby: bool) -> None:
        nonlocal new_player
        target = entities[target_id]
        touched.setdefault(target_id, None)
        for effect in interaction.effects:
            kind = effect.type
            if kind in ("set_var", "add_var"):
                name = _name(effect.var)
                if name not in target.state.vars and len(target.state.vars) >= 8:
                    raise InteractionError(f"Entity {target.id} cannot have more than 8 variables.")
                value = effect.value
                if kind == "add_var":
                    value = max(effect.min, min(effect.max, target.state.vars.get(name, 0.0) + value))
                target.state.vars[name] = value
            elif kind in ("set_param", "scale_param"):
                if not effect.param:
                    raise InteractionError("A geometry parameter name is required.")
                value = effect.value
                if kind == "scale_param":
                    value = max(effect.min, min(effect.max, target.geometry_params.get(effect.param, 1.0) * value))
                target.geometry_params[effect.param] = value
            elif kind == "set_condition":
                if effect.condition is None:
                    raise InteractionError("A condition is required.")
                target.state.condition = Condition(effect.condition)
                if target.state.condition == Condition.removed:
                    target.state.blocks_path = False
                    target.state.integrity = 0.0
                elif target.state.condition == Condition.charred:
                    target.state.integrity = 0.25
            elif kind == "ignite":
                if Capability.flammable in target.capabilities and target.state.condition == Condition.intact:
                    target = ignite(target, tick)
                    entities[target_id] = target
            elif kind == "emit":
                emits.append({"entity_id": target.id, "particles": effect.particles,
                              "color": list(effect.color) if effect.color is not None else None,
                              "count": effect.count, "position": list(target.transform.position)})
            elif kind == "give":
                if not nearby:
                    resource = _name(effect.resource)
                    if new_player is None:
                        new_player = player.model_copy(deep=True)
                    new_player.inventory[resource] = new_player.inventory.get(resource, 0) + effect.amount
            elif kind == "trigger_nearby":
                if nearby:
                    continue
                x, _, z = target.transform.position
                candidates = []
                for other in entities.values():
                    if (other.id == target.id or other.state.condition == Condition.removed
                            or (effect.same_kind and other.kind != target.kind)):
                        continue
                    ox, _, oz = other.transform.position
                    distance_squared = (ox - x) ** 2 + (oz - z) ** 2
                    if distance_squared <= effect.radius_m ** 2:
                        candidates.append((distance_squared, other.id))
                for _, other_id in sorted(candidates)[:effect.max_targets]:
                    other = entities[other_id]
                    behavior = behaviors.get(other.geometry_id)
                    followup = next((item for item in behavior.interactions if item.id == effect.interaction),
                                    None) if behavior else None
                    if followup is not None and not check(followup, other):
                        apply(followup, other_id, nearby=True)
                        triggered[other_id] = followup.id
            else:
                raise InteractionError(f"Unknown interaction effect: {kind!r}")

    apply(spec, entity.id, nearby=False)
    changed = [entities[key] for key in touched if entities[key] != originals[key]]
    payload = {"interaction": spec.id, "verb": spec.verb, "emits": emits, "triggered": triggered}
    result = Effect(EventType.entity_interacted, changed, payload,
                    message=spec.message or f"You {spec.verb} the {entity.kind}.")
    return [result], new_player, emits
