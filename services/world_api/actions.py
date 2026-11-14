"""Action registry: the simulation, not the language model, decides what an action may do.

An ActionDef is data plus two pure functions:
  check(ctx)  -> list[PreconditionFailure]   (empty = allowed)
  apply(ctx)  -> list[Effect]                (entity replacements + the event describing them)
Neither touches the database; the mutation transaction in service.py persists the returned effects.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

from greality_contracts import (
    ActionType,
    Capability,
    Condition,
    EnvironmentState,
    EventType,
    PlayerState,
    PreconditionFailure,
    WorldEntity,
)


@dataclass
class ActionContext:
    tick: int
    environment: EnvironmentState
    player: PlayerState
    player_position: tuple[float, float, float]
    target: Optional[WorldEntity]
    distance_m: float


@dataclass
class Effect:
    type: EventType
    entities: list[WorldEntity]  # full post-change entities
    payload: dict = field(default_factory=dict)
    player: Optional[PlayerState] = None  # replaced player state, if the action changes it
    message: str = ""


@dataclass(frozen=True)
class ActionDef:
    type: ActionType
    max_range_m: float
    capability: Optional[Capability]
    allowed_conditions: tuple[Condition, ...]
    extra_check: Callable[[ActionContext], list[PreconditionFailure]]
    apply: Callable[[ActionContext], list[Effect]]
    mutates: bool = True


def distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.dist(a, b)


def evaluate(action: ActionDef, ctx: ActionContext) -> tuple[bool, bool, bool, list[PreconditionFailure]]:
    """Returns (in_range, has_capability, state_ok, failures)."""
    if ctx.target is None:
        return False, False, False, [PreconditionFailure(code="no_target", message="Nothing here to act on.")]
    t = ctx.target
    failures: list[PreconditionFailure] = []
    reach = action.max_range_m + min(t.bound_radius, 3.0) * 0.5
    in_range = ctx.distance_m <= reach
    if not in_range:
        failures.append(PreconditionFailure(
            code="out_of_range",
            message=f"The {t.kind} is {ctx.distance_m:.1f} m away; get within {reach:.1f} m."))
    has_cap = action.capability is None or action.capability in t.capabilities
    if not has_cap:
        failures.append(PreconditionFailure(
            code="missing_capability", message=f"A {t.kind} cannot be targeted by '{action.type.value}'."))
    state_ok = t.state.condition in action.allowed_conditions
    if not state_ok:
        failures.append(PreconditionFailure(
            code="invalid_state", message=f"The {t.kind} is already {t.state.condition.value}."))
    if not failures:
        extra = action.extra_check(ctx)
        failures.extend(extra)
        state_ok = state_ok and not extra
    return in_range, has_cap, state_ok, failures


def _none(_: ActionContext) -> list[PreconditionFailure]:
    return []


def _ignite_check(ctx: ActionContext) -> list[PreconditionFailure]:
    if ctx.environment.humidity >= 0.9:
        return [PreconditionFailure(code="environment", message="Everything is too wet to catch fire.")]
    if ctx.target and ctx.target.state.fuel <= 0.0:
        return [PreconditionFailure(code="invalid_state", message="There is nothing left to burn.")]
    return []


def _ignite(ctx: ActionContext) -> list[Effect]:
    t = ctx.target.model_copy(deep=True)
    t.state.condition = Condition.burning
    t.state.burn_intensity = 0.35
    t.state.burn_started_tick = ctx.tick
    t.state.hazard = 0.05
    t.state.hazard_radius = min(100.0, t.bound_radius * 0.6 + 2.0)
    t.geometry_params = {**t.geometry_params, "burn": 0.2}
    return [Effect(EventType.entity_ignited, [t], {"intensity": 0.35}, message=f"The {t.kind} catches fire.")]


def _extinguish(ctx: ActionContext) -> list[Effect]:
    t = ctx.target.model_copy(deep=True)
    burned = float(t.geometry_params.get("burn", 0.0))
    t.state.condition = Condition.charred if burned >= 0.6 else Condition.intact
    t.state.burn_intensity = 0.0
    t.state.hazard = 0.0
    t.state.hazard_radius = 0.0
    return [Effect(EventType.entity_extinguished, [t], message=f"You put out the {t.kind}.")]


def _remove(ctx: ActionContext) -> list[Effect]:
    t = ctx.target.model_copy(deep=True)
    gained = {k: v for k, v in t.state.resources.items() if v > 0} if Capability.harvestable in t.capabilities else {}
    player = ctx.player.model_copy(deep=True)
    for name, amount in gained.items():
        player.inventory[name] = player.inventory.get(name, 0) + amount
    t.state.condition = Condition.removed
    t.state.resources = {}
    t.state.blocks_path = False
    t.state.integrity = 0.0
    got = ", ".join(f"{v} {k}" for k, v in gained.items())
    msg = f"You remove the {t.kind}" + (f" and collect {got}." if got else ".")
    return [Effect(EventType.entity_removed, [t], {"gained": gained}, player=player if gained else None, message=msg)]


def _inspect(ctx: ActionContext) -> list[Effect]:
    t = ctx.target
    res = ", ".join(f"{v} {k}" for k, v in t.state.resources.items()) or "nothing usable"
    return [Effect(EventType.resource_changed, [], message=f"A {t.state.condition.value} {t.kind} holding {res}.")]


ACTIONS: dict[ActionType, ActionDef] = {
    ActionType.ignite: ActionDef(ActionType.ignite, 6.0, Capability.flammable, (Condition.intact,), _ignite_check, _ignite),
    ActionType.extinguish: ActionDef(ActionType.extinguish, 6.0, None, (Condition.burning,), _none, _extinguish),
    ActionType.remove: ActionDef(ActionType.remove, 4.0, Capability.removable,
                                 (Condition.intact, Condition.charred), _none, _remove),
    ActionType.inspect: ActionDef(ActionType.inspect, 12.0, None,
                                  (Condition.intact, Condition.burning, Condition.charred), _none, _inspect,
                                  mutates=False),
}
