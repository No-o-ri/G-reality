"""Authoritative world service: every read and write of world state goes through here.

Mutation protocol (docs/architecture/ARCHITECTURE.md §7.3): validate → interpret (advisory) →
BEGIN, lock the world row → re-validate against current rows → apply pure effects → write entities,
one event per world revision, region revisions, neural invalidation → COMMIT → return MutationResult.
Nothing is visible to any reader unless the whole transaction commits.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from greality_contracts import (
    CONTRACT_VERSION,
    REGION_SIZE_M,
    ActionPreconditions,
    ActionType,
    DirtyRegion,
    EnvironmentState,
    EventType,
    GameplayState,
    GenerationStatus,
    JobState,
    MutationResult,
    NeuralAssetManifest,
    NeuralGenerationJob,
    NeuralMetrics,
    ParsedIntent,
    PlayerState,
    PreconditionFailure,
    QualityProfile,
    RegionCoord,
    RegionSnapshot,
    ResolvedTarget,
    Transform,
    ValidatedAction,
    WorldCommand,
    WorldEntity,
    WorldEvent,
    WorldRegion,
    WorldSpec,
    WorldState,
)

from .actions import ACTIONS, ActionContext, distance, evaluate
from .interpreter import CommandContext, IntentDraft, Interpreter, InterpreterError, RulesInterpreter
from .regions import GENERATOR_VERSION, generate_region, region_of, regions_touching, sphere_aabb
from .worldspec import build_spec

PLAYER_ID = "p1"
LIVE_JOB_STATES = ("queued", "awaiting_views", "training", "validating")
SNAPSHOT_EVERY = 25
MAX_REGIONS_PER_REQUEST = 49


class NotFound(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _entity_from_row(r: dict) -> WorldEntity:
    return WorldEntity(
        id=r["entity_id"], world_id=r["world_id"], kind=r["kind"], geometry_id=r["geometry_id"],
        geometry_params=r["geometry_params"],
        transform=Transform(position=(r["px"], r["py"], r["pz"]), rotation=(r["qx"], r["qy"], r["qz"], r["qw"]),
                            scale=r["scale"]),
        region=RegionCoord(rx=r["rx"], rz=r["rz"]), bound_radius=r["bound_radius"],
        capabilities=r["capabilities"], state=r["state"], revision=r["revision"],
        created_revision=r["created_revision"], origin=r["origin"],
    )


def _entity_params(e: WorldEntity) -> tuple:
    p, q = e.transform.position, e.transform.rotation
    return (
        e.world_id, e.id, e.kind, e.geometry_id, Jsonb(e.geometry_params), p[0], p[1], p[2], q[0], q[1], q[2], q[3],
        e.transform.scale, e.region.rx, e.region.rz, e.bound_radius, [c.value for c in e.capabilities],
        e.state.condition.value, Jsonb(e.state.model_dump(mode="json")), e.origin, e.revision, e.created_revision,
    )


_ENTITY_INSERT = """
INSERT INTO entities (world_id, entity_id, kind, geometry_id, geometry_params, px, py, pz, qx, qy, qz, qw, scale,
                      rx, rz, bound_radius, capabilities, condition, state, origin, revision, created_revision)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
"""
_ENTITY_UPSERT = _ENTITY_INSERT + """
ON CONFLICT (world_id, entity_id) DO UPDATE SET
    geometry_params = EXCLUDED.geometry_params, capabilities = EXCLUDED.capabilities,
    condition = EXCLUDED.condition, state = EXCLUDED.state, revision = EXCLUDED.revision,
    px = EXCLUDED.px, py = EXCLUDED.py, pz = EXCLUDED.pz, scale = EXCLUDED.scale
"""


def _job_from_row(r: dict) -> NeuralGenerationJob:
    return NeuralGenerationJob(
        job_id=r["job_id"], world_id=r["world_id"], coord=RegionCoord(rx=r["rx"], rz=r["rz"]),
        source_region_revision=r["source_region_revision"], reason=r["reason"], priority=r["priority"],
        state=r["state"], quality=r["quality"], attempt=r["attempt"], error=r["error"], metrics=r["metrics"],
        asset_id=r["asset_id"], created_at=r["created_at"], started_at=r["started_at"], finished_at=r["finished_at"],
    )


def _event_from_row(r: dict) -> WorldEvent:
    return WorldEvent(
        event_id=r["event_id"], world_id=r["world_id"], revision=r["revision"], tick=r["tick"], type=r["type"],
        cause=r["cause"], command_id=r["command_id"], parent_event_id=r["parent_event_id"],
        entity_ids=r["entity_ids"], regions=r["regions"], bounds=r["bounds"], payload=r["payload"],
        created_at=r["created_at"],
    )


class WorldService:
    def __init__(self, pool: ConnectionPool, interpreter: Interpreter):
        self.pool = pool
        self.interpreter = interpreter
        self.rules = RulesInterpreter()
        # Test hook: called inside the mutation transaction after entity writes (used to prove rollback).
        self.fault_hook: Optional[Callable[[], None]] = None

    # ------------------------------------------------------------------ worlds

    def create_world(self, prompt: str, seed: Optional[int] = None, name: Optional[str] = None,
                     world_id: Optional[str] = None) -> WorldState:
        """Interpret the prompt (provider errors propagate as InterpreterError) and commit a new world."""
        draft = self.interpreter.world(prompt)
        seed = seed if seed is not None else uuid.uuid4().int % (2**31)
        spec = build_spec(draft, prompt, seed)
        spec.interpretation_notes.insert(0, f"interpreted by {self.interpreter.name}")
        world_id = world_id or f"w-{uuid.uuid4().hex[:12]}"
        player = PlayerState(player_id=PLAYER_ID, position=spec.spawn)
        gameplay = GameplayState()
        now = _now()
        event_id = f"ev-{uuid.uuid4().hex[:16]}"
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                """INSERT INTO worlds (world_id, name, seed, revision, generator_version, region_size_m,
                                       contract_version, status, spec, environment, gameplay, created_at, updated_at)
                   VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (world_id, (name or spec.title)[:80], seed, GENERATOR_VERSION, REGION_SIZE_M, CONTRACT_VERSION,
                 GenerationStatus.geometry_ready.value, Jsonb(spec.model_dump(mode="json")),
                 Jsonb(spec.environment.model_dump(mode="json")), Jsonb(gameplay.model_dump(mode="json")), now, now),
            )
            for g in spec.archetypes:
                conn.execute(
                    "INSERT INTO geometry_definitions (world_id, geometry_id, version, definition) VALUES (%s,%s,%s,%s)",
                    (world_id, g.id, g.version, Jsonb(g.model_dump(mode="json"))),
                )
            conn.execute("INSERT INTO players (world_id, player_id, state) VALUES (%s,%s,%s)",
                         (world_id, PLAYER_ID, Jsonb(player.model_dump(mode="json"))))
            conn.execute(
                """INSERT INTO world_events (world_id, revision, event_id, tick, type, cause, payload, created_at)
                   VALUES (%s,1,%s,0,%s,'system',%s,%s)""",
                (world_id, event_id, EventType.world_created.value,
                 Jsonb({"prompt": prompt[:2000], "seed": seed, "interpreter": self.interpreter.name}), now),
            )
        return self.get_world(world_id)

    def get_world(self, world_id: str) -> WorldState:
        with self.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            w = cur.execute("SELECT * FROM worlds WHERE world_id = %s", (world_id,)).fetchone()
            if w is None:
                raise NotFound(f"world {world_id} does not exist")
            p = cur.execute("SELECT state FROM players WHERE world_id = %s AND player_id = %s",
                            (world_id, PLAYER_ID)).fetchone()
        return WorldState(
            contract_version=w["contract_version"], world_id=w["world_id"], name=w["name"], seed=w["seed"],
            revision=w["revision"], generator_version=w["generator_version"], region_size_m=w["region_size_m"],
            spec=w["spec"], environment=w["environment"], gameplay=w["gameplay"], player=p["state"],
            status=w["status"], created_at=w["created_at"], updated_at=w["updated_at"],
        )

    def list_worlds(self) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            return cur.execute(
                """SELECT world_id, name, seed, revision, status, generator_version, created_at, updated_at
                   FROM worlds ORDER BY created_at DESC"""
            ).fetchall()

    def delete_world(self, world_id: str) -> None:
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("DELETE FROM neural_assets WHERE world_id = %s", (world_id,))
            conn.execute("DELETE FROM neural_jobs WHERE world_id = %s", (world_id,))
            conn.execute("DELETE FROM worlds WHERE world_id = %s", (world_id,))

    def update_player(self, world_id: str, player: PlayerState) -> None:
        with self.pool.connection() as conn:
            n = conn.execute(
                "UPDATE players SET state = %s, updated_at = now() WHERE world_id = %s AND player_id = %s",
                (Jsonb(player.model_dump(mode="json")), world_id, player.player_id)).rowcount
        if n == 0:
            raise NotFound(f"player {player.player_id} in world {world_id} does not exist")

    # ------------------------------------------------------------------ regions

    def load_regions(self, world_id: str, coords: list[RegionCoord]) -> list[RegionSnapshot]:
        """Return snapshots, materializing (generating + committing) any region seen for the first time."""
        coords = list({(c.rx, c.rz): c for c in coords}.values())[:MAX_REGIONS_PER_REQUEST]
        out: list[RegionSnapshot] = []
        with self.pool.connection() as conn, conn.transaction():
            cur = conn.cursor(row_factory=dict_row)
            w = cur.execute("SELECT seed, spec, generator_version FROM worlds WHERE world_id = %s",
                            (world_id,)).fetchone()
            if w is None:
                raise NotFound(f"world {world_id} does not exist")
            spec = WorldSpec.model_validate(w["spec"])
            # Set-based: a fixed number of round trips regardless of how many regions are requested.
            xs, zs = [c.rx for c in coords], [c.rz for c in coords]
            pairs = "(rx, rz) IN (SELECT * FROM unnest(%s::int[], %s::int[]))"
            cur.execute(
                """INSERT INTO world_regions (world_id, rx, rz, generator_version)
                   SELECT %s, t.x, t.z, %s FROM unnest(%s::int[], %s::int[]) AS t(x, z)
                   ON CONFLICT DO NOTHING""", (world_id, w["generator_version"], xs, zs))
            regions = {(r["rx"], r["rz"]): r for r in cur.execute(
                f"SELECT * FROM world_regions WHERE world_id=%s AND {pairs} ORDER BY rx, rz FOR UPDATE",
                (world_id, xs, zs)).fetchall()}
            fresh = [c for c in coords if not regions[(c.rx, c.rz)]["materialized"]]
            if fresh:
                generated = [e for c in fresh
                             for e in generate_region(world_id, spec, w["seed"], c, w["generator_version"])]
                if generated:
                    cur.executemany(_ENTITY_INSERT + " ON CONFLICT DO NOTHING", [_entity_params(e) for e in generated])
                cur.execute(f"UPDATE world_regions SET materialized = true WHERE world_id=%s AND {pairs}",
                            (world_id, [c.rx for c in fresh], [c.rz for c in fresh]))
            by_region: dict[tuple[int, int], list[WorldEntity]] = {(c.rx, c.rz): [] for c in coords}
            for row in cur.execute(f"SELECT * FROM entities WHERE world_id=%s AND {pairs} ORDER BY entity_id",
                                   (world_id, xs, zs)).fetchall():
                by_region[(row["rx"], row["rz"])].append(_entity_from_row(row))
            assets = {(r["rx"], r["rz"]): r for r in cur.execute(
                f"""SELECT rx, rz, region_revision, manifest FROM neural_assets
                    WHERE world_id=%s AND state='active' AND {pairs}""", (world_id, xs, zs)).fetchall()}
            jobs = {(r["rx"], r["rz"]): r for r in cur.execute(
                f"SELECT * FROM neural_jobs WHERE world_id=%s AND state = ANY(%s) AND {pairs}",
                (world_id, list(LIVE_JOB_STATES), xs, zs)).fetchall()}
            for c in coords:
                key = (c.rx, c.rz)
                r = regions[key]
                a = assets.get(key)
                # An asset is only ever returned if it was built from exactly the current region revision.
                current = a is not None and a["region_revision"] == r["revision"]
                out.append(RegionSnapshot(
                    region=WorldRegion(world_id=world_id, coord=c, revision=r["revision"], materialized=True,
                                       generator_version=r["generator_version"], smoke_density=r["smoke_density"],
                                       hazard_level=r["hazard_level"]),
                    entities=by_region[key],
                    neural_asset=a["manifest"] if current else None,
                    pending_job=_job_from_row(jobs[key]) if key in jobs else None,
                ))
        return out

    # ------------------------------------------------------------------ commands

    def _interpret(self, text: str, context: CommandContext) -> tuple[IntentDraft, str, Optional[str]]:
        """Primary provider first; on provider failure fall back to rules and say so."""
        try:
            return self.interpreter.command(text, context), self.interpreter.kind, None
        except InterpreterError as exc:
            return self.rules.command(text, context), "rules", f"{exc.code}: {exc.message}"

    @staticmethod
    def _pick_target(entities: list[WorldEntity], cmd: WorldCommand, kind: Optional[str]
                     ) -> tuple[Optional[WorldEntity], str]:
        alive = [e for e in entities if e.state.condition.value != "removed"]
        if cmd.target_hint:
            hinted = next((e for e in alive if e.id == cmd.target_hint), None)
            if hinted is not None and (kind is None or hinted.kind == kind):
                return hinted, "hint"
        pool_ = [e for e in alive if kind is None or e.kind == kind]
        vx, vy, vz = cmd.view_direction
        vlen = math.sqrt(vx * vx + vy * vy + vz * vz) or 1.0
        best, best_score = None, 1e9
        for e in pool_:
            d = distance(cmd.player_position, e.transform.position)
            if d > 40.0:
                continue
            to = [e.transform.position[i] + (e.bound_radius * 0.4 if i == 1 else 0.0) - cmd.player_position[i]
                  for i in range(3)]
            tlen = math.sqrt(sum(c * c for c in to)) or 1.0
            cos = (to[0] * vx + to[1] * vy + to[2] * vz) / (tlen * vlen)
            if cos > 0.85 and d * (1.3 - cos) < best_score:
                best, best_score = e, d * (1.3 - cos)
        if best is not None:
            return best, "aim"
        near = min(pool_, key=lambda e: distance(cmd.player_position, e.transform.position), default=None)
        return near, "nearest"

    def handle_command(self, cmd: WorldCommand) -> MutationResult:
        stored = self._stored_result(cmd.world_id, cmd.command_id)
        if stored is not None:
            return stored

        # Advisory phase (no locks): gather candidates for the interpreter and for target resolution.
        here = region_of(cmd.player_position[0], cmd.player_position[2])
        with self.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            if cur.execute("SELECT 1 FROM worlds WHERE world_id=%s", (cmd.world_id,)).fetchone() is None:
                raise NotFound(f"world {cmd.world_id} does not exist")
            rows = cur.execute(
                """SELECT * FROM entities WHERE world_id=%s AND rx BETWEEN %s AND %s AND rz BETWEEN %s AND %s""",
                (cmd.world_id, here.rx - 1, here.rx + 1, here.rz - 1, here.rz + 1)).fetchall()
        nearby = [_entity_from_row(r) for r in rows]
        aimed, _ = self._pick_target(nearby, cmd, None)
        context = CommandContext(
            nearby_kinds=sorted({e.kind for e in nearby
                                 if distance(cmd.player_position, e.transform.position) < 25.0}),
            aimed_kind=aimed.kind if aimed else None,
        )
        draft, interpreter_kind, interpreter_note = self._interpret(cmd.text, context)
        candidate, resolved_by = self._pick_target(nearby, cmd, draft.target_kind)

        try:
            return self._commit_command(cmd, draft, interpreter_kind, interpreter_note, candidate, resolved_by)
        except NotFound:
            raise
        except Exception as exc:  # database / transport failure: nothing was committed
            current = self._world_revision(cmd.world_id)
            return MutationResult(status="error", command_id=cmd.command_id, world_revision=current,
                                  message=f"The change could not be saved ({type(exc).__name__}); nothing was applied.")

    def _world_revision(self, world_id: str) -> int:
        try:
            with self.pool.connection() as conn:
                row = conn.execute("SELECT revision FROM worlds WHERE world_id=%s", (world_id,)).fetchone()
                return row[0] if row else 0
        except Exception:
            return 0

    def _stored_result(self, world_id: str, command_id: str) -> Optional[MutationResult]:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT result FROM commands WHERE world_id=%s AND command_id=%s",
                               (world_id, command_id)).fetchone()
        return MutationResult.model_validate(row[0]) if row else None

    def _commit_command(self, cmd: WorldCommand, draft: IntentDraft, interpreter_kind: str,
                        interpreter_note: Optional[str], candidate: Optional[WorldEntity], resolved_by: str
                        ) -> MutationResult:
        with self.pool.connection() as conn, conn.transaction():
            cur = conn.cursor(row_factory=dict_row)
            # Serializes all writers of this world; revisions are assigned under this lock.
            w = cur.execute("SELECT revision, environment, gameplay FROM worlds WHERE world_id=%s FOR UPDATE",
                            (cmd.world_id,)).fetchone()
            if w is None:
                raise NotFound(f"world {cmd.world_id} does not exist")
            again = cur.execute("SELECT result FROM commands WHERE world_id=%s AND command_id=%s",
                                (cmd.world_id, cmd.command_id)).fetchone()
            if again:
                return MutationResult.model_validate(again["result"])
            base_revision = w["revision"]
            env = EnvironmentState.model_validate(w["environment"])
            gameplay = GameplayState.model_validate(w["gameplay"])
            prow = cur.execute("SELECT state FROM players WHERE world_id=%s AND player_id=%s FOR UPDATE",
                               (cmd.world_id, cmd.player_id)).fetchone()
            if prow is None:
                raise NotFound(f"player {cmd.player_id} does not exist")
            player = PlayerState.model_validate(prow["state"])

            target: Optional[WorldEntity] = None
            if candidate is not None:  # re-read the authoritative row under the lock
                trow = cur.execute("SELECT * FROM entities WHERE world_id=%s AND entity_id=%s",
                                   (cmd.world_id, candidate.id)).fetchone()
                target = _entity_from_row(trow) if trow else None

            failures: list[PreconditionFailure] = []
            action_def = None
            if draft.action == "unknown" or ActionType(draft.action) not in ACTIONS:
                failures.append(PreconditionFailure(
                    code="unknown_action",
                    message="I don't know how to do that here. Try: set on fire, put out, remove, inspect."))
                in_range = has_cap = state_ok = False
                max_range = 0.0
            else:
                action_def = ACTIONS[ActionType(draft.action)]
                dist_m = distance(cmd.player_position, target.transform.position) if target else 0.0
                ctx = ActionContext(tick=gameplay.tick, environment=env, player=player,
                                    player_position=cmd.player_position, target=target, distance_m=dist_m)
                in_range, has_cap, state_ok, failures = evaluate(action_def, ctx)
                max_range = action_def.max_range_m

            intent = ParsedIntent(
                action=ActionType(draft.action) if draft.action != "unknown" else ActionType.inspect,
                target_kind=draft.target_kind, interpreter=interpreter_kind, confidence=draft.confidence)
            validated = None
            if target is not None:
                validated = ValidatedAction(
                    command_id=cmd.command_id, intent=intent,
                    target=ResolvedTarget(entity_id=target.id, kind=target.kind, position=target.transform.position,
                                          distance_m=distance(cmd.player_position, target.transform.position),
                                          region=target.region, resolved_by=resolved_by),
                    preconditions=ActionPreconditions(max_range_m=max_range, in_range=in_range,
                                                      has_capability=has_cap, state_ok=state_ok, failures=failures),
                    base_world_revision=base_revision)
            note = f" (language model unavailable, used keyword rules: {interpreter_note})" if interpreter_note else ""

            if failures:
                stale = (cmd.expected_world_revision is not None and target is not None
                         and target.revision > cmd.expected_world_revision)
                result = MutationResult(
                    status="conflict" if stale else "rejected", command_id=cmd.command_id,
                    world_revision=base_revision, message=failures[0].message + note, action=validated,
                    failures=failures)
                self._store_command(cur, cmd, intent, result)
                return result

            effects = action_def.apply(ctx)
            if not action_def.mutates:
                result = MutationResult(status="committed", command_id=cmd.command_id, world_revision=base_revision,
                                        message=effects[0].message + note, action=validated)
                self._store_command(cur, cmd, intent, result)
                return result

            revision = base_revision
            events: list[WorldEvent] = []
            changed: dict[str, WorldEntity] = {}
            dirty: dict[tuple[int, int], DirtyRegion] = {}
            now = _now()
            for effect in effects:
                revision += 1
                event_id = f"ev-{uuid.uuid4().hex[:16]}"
                bounds = None
                coords: list[RegionCoord] = []
                docs: dict[str, Any] = {}
                for e in effect.entities:
                    # the target row was read under the world lock above; other entities are looked up here
                    before = changed.get(e.id) or (target if target is not None and target.id == e.id else None)
                    if before is None:
                        row = cur.execute("SELECT * FROM entities WHERE world_id=%s AND entity_id=%s",
                                          (cmd.world_id, e.id)).fetchone()
                        before = _entity_from_row(row) if row else None
                    e.revision = revision
                    cur.execute(_ENTITY_UPSERT, _entity_params(e))
                    docs[e.id] = {
                        "before": before.model_dump(mode="json") if before else None,
                        "after": e.model_dump(mode="json"),
                    }
                    changed[e.id] = e
                    box = sphere_aabb(e.transform.position, e.bound_radius)
                    bounds = box if bounds is None else type(box)(
                        min=tuple(min(a, b) for a, b in zip(bounds.min, box.min)),
                        max=tuple(max(a, b) for a, b in zip(bounds.max, box.max)))
                if bounds is not None:
                    coords = regions_touching(bounds)
                if effect.player is not None:
                    player = effect.player
                    cur.execute("UPDATE players SET state=%s, updated_at=now() WHERE world_id=%s AND player_id=%s",
                                (Jsonb(player.model_dump(mode="json")), cmd.world_id, cmd.player_id))
                event = WorldEvent(
                    event_id=event_id, world_id=cmd.world_id, revision=revision, tick=gameplay.tick,
                    type=effect.type, cause="player", command_id=cmd.command_id,
                    entity_ids=[e.id for e in effect.entities], regions=coords, bounds=bounds,
                    payload={**effect.payload, "entities": docs,
                             "player": player.model_dump(mode="json") if effect.player else None},
                    created_at=now)
                cur.execute(
                    """INSERT INTO world_events (world_id, revision, event_id, tick, type, cause, command_id,
                                                 entity_ids, regions, bounds, payload, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (event.world_id, event.revision, event.event_id, event.tick, event.type.value, event.cause,
                     event.command_id, event.entity_ids, Jsonb([c.model_dump() for c in coords]),
                     Jsonb(bounds.model_dump(mode="json")) if bounds else None, Jsonb(event.payload), now))
                events.append(event)
                for d in self._bump_regions(cur, cmd.world_id, coords, revision, event_id, bounds):
                    dirty[(d.coord.rx, d.coord.rz)] = d

            if self.fault_hook is not None:
                self.fault_hook()

            cur.execute("UPDATE worlds SET revision=%s, updated_at=now() WHERE world_id=%s", (revision, cmd.world_id))
            result = MutationResult(
                status="committed", command_id=cmd.command_id, world_revision=revision,
                message=" ".join(e.message for e in effects if e.message) + note, action=validated,
                events=events, entities=list(changed.values()), dirty_regions=list(dirty.values()))
            self._store_command(cur, cmd, intent, result)
            if revision // SNAPSHOT_EVERY != base_revision // SNAPSHOT_EVERY:
                self._write_snapshot(cur, cmd.world_id, revision, gameplay.tick)
            return result

    @staticmethod
    def _store_command(cur, cmd: WorldCommand, intent: ParsedIntent, result: MutationResult) -> None:
        cur.execute(
            """INSERT INTO commands (world_id, command_id, player_id, text, intent, status, result, world_revision)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (cmd.world_id, cmd.command_id, cmd.player_id, cmd.text, Jsonb(intent.model_dump(mode="json")),
             result.status, Jsonb(result.model_dump(mode="json")), result.world_revision))

    @staticmethod
    def _bump_regions(cur, world_id: str, coords: list[RegionCoord], world_revision: int, event_id: str, bounds
                      ) -> list[DirtyRegion]:
        """Region revision +1, history row, supersede the splat asset and any live job, requeue if needed."""
        if not coords:
            return []
        # Set-based statements: four round trips regardless of how many regions the change touches.
        xs, zs = [c.rx for c in coords], [c.rz for c in coords]
        revs = {(r["rx"], r["rz"]): r["revision"] for r in cur.execute(
            """INSERT INTO world_regions (world_id, rx, rz, revision)
               SELECT %s, t.x, t.z, 1 FROM unnest(%s::int[], %s::int[]) AS t(x, z)
               ON CONFLICT (world_id, rx, rz) DO UPDATE SET revision = world_regions.revision + 1, updated_at = now()
               RETURNING rx, rz, revision""", (world_id, xs, zs)).fetchall()}
        cur.execute(
            """INSERT INTO region_revisions (world_id, rx, rz, revision, world_revision, event_id)
               SELECT %s, t.x, t.z, t.r, %s, %s FROM unnest(%s::int[], %s::int[], %s::bigint[]) AS t(x, z, r)""",
            (world_id, world_revision, event_id, xs, zs, [revs[(c.rx, c.rz)] for c in coords]))
        stale = {(r["rx"], r["rz"]): r["asset_id"] for r in cur.execute(
            """UPDATE neural_assets SET state='superseded' WHERE world_id=%s AND state='active'
               AND (rx, rz) IN (SELECT * FROM unnest(%s::int[], %s::int[])) RETURNING rx, rz, asset_id""",
            (world_id, xs, zs)).fetchall()}
        old_jobs = {(r["rx"], r["rz"]): r for r in cur.execute(
            """UPDATE neural_jobs SET state='superseded', finished_at=now() WHERE world_id=%s AND state = ANY(%s)
               AND (rx, rz) IN (SELECT * FROM unnest(%s::int[], %s::int[])) RETURNING rx, rz, quality, priority""",
            (world_id, list(LIVE_JOB_STATES), xs, zs)).fetchall()}
        out: list[DirtyRegion] = []
        for c in coords:
            key = (c.rx, c.rz)
            if key in stale or key in old_jobs:  # region is in use as far field: regenerate for the new revision
                old = old_jobs.get(key)
                cur.execute(
                    """INSERT INTO neural_jobs (job_id, world_id, rx, rz, source_region_revision, reason, priority,
                                                state, quality) VALUES (%s,%s,%s,%s,%s,'mutation',%s,'queued',%s)""",
                    (f"job-{uuid.uuid4().hex[:16]}", world_id, c.rx, c.rz, revs[key],
                     old["priority"] if old else 0.0, old["quality"] if old else "balanced"))
            out.append(DirtyRegion(world_id=world_id, coord=c, region_revision=revs[key], event_id=event_id,
                                   bounds=bounds, stale_asset_id=stale.get(key)))
        return out

    @staticmethod
    def _write_snapshot(cur, world_id: str, revision: int, tick: int) -> None:
        rows = cur.execute("SELECT * FROM entities WHERE world_id=%s ORDER BY entity_id", (world_id,)).fetchall()
        doc = {"entities": [_entity_from_row(r).model_dump(mode="json") for r in rows]}
        cur.execute("INSERT INTO world_snapshots (world_id, revision, tick, snapshot) VALUES (%s,%s,%s,%s)",
                    (world_id, revision, tick, Jsonb(doc)))

    # ------------------------------------------------------------------ history

    def events(self, world_id: str, after_revision: int = 0, limit: int = 200) -> list[WorldEvent]:
        with self.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute(
                "SELECT * FROM world_events WHERE world_id=%s AND revision > %s ORDER BY revision LIMIT %s",
                (world_id, after_revision, min(limit, 1000))).fetchall()
        return [_event_from_row(r) for r in rows]

    def entities(self, world_id: str) -> dict[str, WorldEntity]:
        with self.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute("SELECT * FROM entities WHERE world_id=%s", (world_id,)).fetchall()
        return {r["entity_id"]: _entity_from_row(r) for r in rows}

    def replay(self, world_id: str) -> dict[str, WorldEntity]:
        """Rebuild entity state from first principles: deterministic generator output + the event log."""
        world = self.get_world(world_id)
        with self.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            regions = cur.execute("SELECT rx, rz FROM world_regions WHERE world_id=%s AND materialized",
                                  (world_id,)).fetchall()
        state: dict[str, WorldEntity] = {}
        for r in regions:
            for e in generate_region(world_id, world.spec, world.seed, RegionCoord(rx=r["rx"], rz=r["rz"]),
                                     world.generator_version):
                state[e.id] = e
        for event in self.events(world_id, 0, 1000):
            for entity_id, doc in (event.payload.get("entities") or {}).items():
                state[entity_id] = WorldEntity.model_validate(doc["after"])
        return state

    # ------------------------------------------------------------------ neural jobs and assets

    def enqueue_job(self, world_id: str, coord: RegionCoord, reason: str = "explore", priority: float = 0.0,
                    quality: QualityProfile = QualityProfile.balanced) -> NeuralGenerationJob:
        """Ensure one live job exists for the region's *current* revision."""
        with self.pool.connection() as conn, conn.transaction():
            cur = conn.cursor(row_factory=dict_row)
            r = cur.execute("SELECT revision FROM world_regions WHERE world_id=%s AND rx=%s AND rz=%s FOR UPDATE",
                            (world_id, coord.rx, coord.rz)).fetchone()
            if r is None:
                raise NotFound(f"region {coord.rx},{coord.rz} is not known; load it first")
            live = cur.execute(
                "SELECT * FROM neural_jobs WHERE world_id=%s AND rx=%s AND rz=%s AND state = ANY(%s)",
                (world_id, coord.rx, coord.rz, list(LIVE_JOB_STATES))).fetchone()
            if live and live["source_region_revision"] == r["revision"]:
                return _job_from_row(live)
            if live:
                cur.execute("UPDATE neural_jobs SET state='superseded', finished_at=now() WHERE job_id=%s",
                            (live["job_id"],))
            row = cur.execute(
                """INSERT INTO neural_jobs (job_id, world_id, rx, rz, source_region_revision, reason, priority,
                                            state, quality) VALUES (%s,%s,%s,%s,%s,%s,%s,'queued',%s) RETURNING *""",
                (f"job-{uuid.uuid4().hex[:16]}", world_id, coord.rx, coord.rz, r["revision"], reason, priority,
                 quality.value)).fetchone()
            return _job_from_row(row)

    def set_job_state(self, job_id: str, state: JobState, error: Optional[str] = None) -> bool:
        """Advance a live job. Returns False if the job is no longer live (e.g. superseded by a mutation)."""
        terminal = state in (JobState.failed, JobState.cancelled)
        with self.pool.connection() as conn:
            n = conn.execute(
                """UPDATE neural_jobs SET state=%s, error=%s,
                          started_at = COALESCE(started_at, CASE WHEN %s = 'training' THEN now() END),
                          finished_at = CASE WHEN %s THEN now() ELSE finished_at END
                   WHERE job_id=%s AND state = ANY(%s)""",
                (state.value, error, state.value, terminal, job_id, list(LIVE_JOB_STATES))).rowcount
        return n == 1

    def jobs(self, world_id: str, limit: int = 100) -> list[NeuralGenerationJob]:
        with self.pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            rows = cur.execute("SELECT * FROM neural_jobs WHERE world_id=%s ORDER BY created_at DESC LIMIT %s",
                               (world_id, limit)).fetchall()
        return [_job_from_row(r) for r in rows]

    def publish_asset(self, job_id: str, *, uri: str, sha256: str, byte_size: int, gaussian_count: int,
                      fmt: str, sh_degree: int, origin, bounds, metrics: NeuralMetrics
                      ) -> tuple[str, Optional[NeuralAssetManifest]]:
        """Compare-and-set publication. A job built from an outdated region revision can never become active.

        Returns ("published", manifest) or ("superseded", None).
        """
        with self.pool.connection() as conn, conn.transaction():
            cur = conn.cursor(row_factory=dict_row)
            job = cur.execute("SELECT * FROM neural_jobs WHERE job_id=%s FOR UPDATE", (job_id,)).fetchone()
            if job is None:
                raise NotFound(f"job {job_id} does not exist")
            region = cur.execute("SELECT revision FROM world_regions WHERE world_id=%s AND rx=%s AND rz=%s FOR UPDATE",
                                 (job["world_id"], job["rx"], job["rz"])).fetchone()
            if job["state"] not in LIVE_JOB_STATES or region["revision"] != job["source_region_revision"]:
                cur.execute(
                    """UPDATE neural_jobs SET state='superseded', finished_at=COALESCE(finished_at, now())
                       WHERE job_id=%s AND state = ANY(%s)""", (job_id, list(LIVE_JOB_STATES)))
                return "superseded", None
            manifest = NeuralAssetManifest(
                asset_id=f"asset-{uuid.uuid4().hex[:16]}", world_id=job["world_id"],
                coord=RegionCoord(rx=job["rx"], rz=job["rz"]), region_revision=job["source_region_revision"],
                state="active", format=fmt, uri=uri, sha256=sha256, byte_size=byte_size,
                gaussian_count=gaussian_count, sh_degree=sh_degree, origin=origin, bounds=bounds, metrics=metrics,
                job_id=job_id, created_at=_now())
            cur.execute("UPDATE neural_assets SET state='superseded' WHERE world_id=%s AND rx=%s AND rz=%s AND state='active'",
                        (job["world_id"], job["rx"], job["rz"]))
            cur.execute(
                """INSERT INTO neural_assets (asset_id, world_id, rx, rz, region_revision, state, method, format, uri,
                                              sha256, byte_size, gaussian_count, manifest, job_id)
                   VALUES (%s,%s,%s,%s,%s,'active',%s,%s,%s,%s,%s,%s,%s,%s)""",
                (manifest.asset_id, manifest.world_id, job["rx"], job["rz"], manifest.region_revision, manifest.method,
                 fmt, uri, sha256, byte_size, gaussian_count, Jsonb(manifest.model_dump(mode="json")), job_id))
            cur.execute(
                "UPDATE neural_jobs SET state='published', asset_id=%s, metrics=%s, finished_at=now() WHERE job_id=%s",
                (manifest.asset_id, Jsonb(metrics.model_dump(mode="json")), job_id))
            return "published", manifest


def make_pool(database_url: str, max_size: int = 6) -> ConnectionPool:
    pool = ConnectionPool(database_url, min_size=1, max_size=max_size, open=False,
                          kwargs={"prepare_threshold": None})  # no server-side prepares through the Neon pooler
    pool.open(wait=True, timeout=30)
    return pool
