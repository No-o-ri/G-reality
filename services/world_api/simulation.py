"""Pure, deterministic fire ticks; callers persist the returned effects."""

from __future__ import annotations

import math
from dataclasses import dataclass

from greality_contracts import Capability, Condition, EnvironmentState, EventType, WorldEntity

from world_api.actions import Effect
from world_api.terrain import stable_unit

MAX_CATCHUP_TICKS = 900
MAX_BURNING = 60
MAX_IGNITIONS_PER_TICK = 6
IGNITION_DELAY_TICKS = 4
CHARRED_CANOPY = 0.05


@dataclass(frozen=True)
class FireRules:
    spread_radius_m: float = 7.0
    base_chance_per_tick: float = 0.05
    wind_weight: float = 0.8


def burn_duration_ticks(entity: WorldEntity) -> int:
    return max(25, min(240, round(entity.state.fuel * 1.2)))


def ignite(entity: WorldEntity, tick: int) -> WorldEntity:
    t = entity.model_copy(deep=True)
    t.state.condition = Condition.burning
    t.state.burn_intensity = 0.35
    t.state.burn_started_tick = tick
    t.state.hazard = 0.05
    t.state.hazard_radius = min(100.0, t.bound_radius * 0.6 + 2.0)
    t.geometry_params = {**t.geometry_params, "burn": 0.2}
    return t


def burn_out(entity: WorldEntity) -> WorldEntity:
    t = entity.model_copy(deep=True)
    t.state.condition = Condition.charred
    t.state.burn_intensity = 0.0
    t.state.fuel = 0.0
    t.state.hazard = 0.0
    t.state.hazard_radius = 0.0
    t.state.integrity = 0.25
    t.state.resources = {}
    t.geometry_params["burn"] = 1.0
    if "canopy" in t.geometry_params:
        t.geometry_params["canopy"] = CHARRED_CANOPY
    return t


def _age(entity: WorldEntity, tick: int) -> int:
    if entity.state.burn_started_tick is None:
        raise ValueError(f"Burning entity {entity.id} has no burn_started_tick")
    return tick - entity.state.burn_started_tick


def visual_burn(entity: WorldEntity, tick: int) -> float:
    if entity.state.condition == Condition.charred:
        return 1.0
    if entity.state.condition == Condition.burning:
        return 0.2 + 0.8 * max(0.0, min(1.0, _age(entity, tick) / burn_duration_ticks(entity)))
    return float(entity.geometry_params.get("burn", 0.0))


def advance(start_tick: int, end_tick: int, burning: list[WorldEntity], candidates: list[WorldEntity],
            environment: EnvironmentState, seed: int, rules: FireRules = FireRules()
            ) -> tuple[int, list[tuple[int, list[Effect]]]]:
    """Advance at most 900 ticks, emitting only burn-outs and ignitions.

    Explicit burning entries win duplicate IDs. Burning states require a start
    tick; an already over-cap input is preserved but cannot ignite more entities.
    Visual progress is derived by visual_burn rather than persisted each second.
    """
    last_tick = max(start_tick, min(end_tick, start_tick + MAX_CATCHUP_TICKS))
    entities = {e.id: e.model_copy(deep=True) for e in candidates}
    entities.update({e.id: e.model_copy(deep=True) for e in burning})
    active = {key: e for key, e in entities.items() if e.state.condition == Condition.burning}
    for e in active.values():
        _age(e, start_tick)

    # Positions never change. A radius no larger than a cell reaches only the
    # source cell and its eight neighbours, including across negative coordinates.
    cell_size = max((rules.spread_radius_m + 0.35 * e.bound_radius for e in entities.values()), default=1.0)
    cell_size = max(cell_size, 0.001)
    grid: dict[tuple[int, int], list[str]] = {}
    for key, e in entities.items():
        x, _, z = e.transform.position
        cell = (math.floor(x / cell_size), math.floor(z / cell_size))
        grid.setdefault(cell, []).append(key)

    wind_x, wind_z = environment.wind
    speed = math.hypot(wind_x, wind_z)
    dryness = (1.0 - environment.humidity) ** 1.5
    changes = []
    for tick in range(start_tick + 1, last_tick + 1):
        effects = []
        expired = [burn_out(active[key]) for key in sorted(active)
                   if _age(active[key], tick) >= burn_duration_ticks(active[key])]
        if expired:
            for e in expired:
                entities[e.id] = e
                del active[e.id]
            effects.append(Effect(EventType.entity_burned_out, expired, {"ids": [e.id for e in expired]},
                                  message="The fire burns out."))

        ignited = []
        sources = {}
        if environment.humidity < 0.9:
            for key in sorted(active):
                if len(active) >= MAX_BURNING or len(ignited) >= MAX_IGNITIONS_PER_TICK:
                    break
                source = active[key]
                if _age(source, tick) < IGNITION_DELAY_TICKS:
                    continue
                sx, _, sz = source.transform.position
                cx, cz = math.floor(sx / cell_size), math.floor(sz / cell_size)
                neighbours = [key for dx in (-1, 0, 1) for dz in (-1, 0, 1)
                              for key in grid.get((cx + dx, cz + dz), [])]
                radius = rules.spread_radius_m + 0.35 * source.bound_radius
                for candidate_id in sorted(neighbours):
                    if len(active) >= MAX_BURNING or len(ignited) >= MAX_IGNITIONS_PER_TICK:
                        break
                    c = entities[candidate_id]
                    if (c.id == source.id or Capability.flammable not in c.capabilities
                            or c.state.condition != Condition.intact or c.state.fuel <= 0.0):
                        continue
                    x, _, z = c.transform.position
                    dx, dz = x - sx, z - sz
                    distance = math.hypot(dx, dz)
                    if radius <= 0.0 or distance > radius:
                        continue
                    align = (dx * wind_x + dz * wind_z) / (distance * speed) if distance and speed else 0.0
                    wind_factor = max(0.15, 1.0 + rules.wind_weight * align * min(speed / 5.0, 1.5))
                    p = (rules.base_chance_per_tick * (1.0 - distance / radius) ** 1.5
                         * wind_factor * dryness * (1.6 if c.kind == "tree" else 1.0))
                    if stable_unit(seed, "fire", tick, source.id, c.id) < p:
                        new = ignite(c, tick)
                        entities[c.id] = active[c.id] = new
                        ignited.append(new)
                        sources[c.id] = source.id
        if ignited:
            effects.append(Effect(EventType.fire_spread, ignited, {"sources": sources},
                                  message="The fire spreads to " + ", ".join(e.id for e in ignited) + "."))
        if effects:
            changes.append((tick, effects))
    return last_tick, changes
