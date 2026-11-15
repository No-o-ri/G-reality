/**
 * Player-facing game loop on top of the engine: authoritative synchronization, the tree interaction, and the visual
 * consequences of fire. Nothing here decides world state: the E key sends a structured command that the server
 * validates and commits to Neon, and the fire's spread and burn-out are simulated by the server. This module only
 * (a) shows what the server committed, as soon as it is committed, and (b) derives in-between visuals (charring
 * progress, flame strength, shrinking foliage) from the authoritative burn_started_tick and the synchronized tick,
 * using the same formula as services/world_api/simulation.py.
 */
import type { MutationResult, RegionCoord, WorldEntity, WorldSync } from "@contracts";
import type { Companion } from "./api";
import type { Engine } from "./Engine";
import type { DynamicVisual } from "../rendering/nearfield/NearField";
import type { FireSource } from "../rendering/effects/FireSystem";

const POLL_MS = 1000;
const SAVE_PLAYER_MS = 4000;
const SMOULDER_S = 50;
const ACTION_RANGE_M = 6;

/** Mirror of simulation.burn_duration_ticks. */
export function burnDurationTicks(e: WorldEntity): number {
  return Math.max(25, Math.min(240, Math.round((e.state?.fuel ?? 0) * 1.2)));
}

/** Mirror of simulation.visual_burn: 0.2 at ignition rising to 1 at burn-out. */
export function visualBurn(e: WorldEntity, tick: number): number {
  const c = e.state?.condition ?? "intact";
  if (c === "charred") return 1;
  if (c === "burning") {
    const age = tick - (e.state?.burn_started_tick ?? tick);
    return 0.2 + 0.8 * Math.max(0, Math.min(1, age / burnDurationTicks(e)));
  }
  return Number((e.geometry_params as Record<string, number> | undefined)?.burn ?? 0);
}

export interface InteractionPrompt {
  entityId: string;
  kind: string;
  label: string; // e.g. "E — Burn it?"
  enabled: boolean;
  hint?: string;
}

export interface GameplayEvents {
  toast(message: string, tone?: "info" | "fire" | "error"): void;
}

export class Gameplay {
  tick = 0;
  revision = 0;
  private tickAt = performance.now();
  private poll: number | null = null;
  private saving: number | null = null;
  private busy = false;
  private inFlight = false;
  private smoulder = new Map<string, number>(); // entity id -> local time (s) its fire went out
  private lastSaved = "";
  prompt: InteractionPrompt | null = null;
  lastSyncMs: number | null = null;
  syncError: string | null = null;

  constructor(private readonly engine: Engine, private readonly companion: Companion | null, private readonly ui: GameplayEvents) {}

  start(): void {
    const world = this.engine.world;
    if (!world) return;
    this.tick = world.gameplay?.tick ?? 0;
    this.revision = world.revision;
    this.tickAt = performance.now();
    this.engine.onEntitiesChanged = (before, after) => this.noticeChanges(before, after);
    if (this.companion) {
      this.poll = window.setInterval(() => void this.syncOnce(), POLL_MS);
      this.saving = window.setInterval(() => void this.savePlayer(), SAVE_PLAYER_MS);
      void this.syncOnce();
    }
  }

  stop(): void {
    if (this.poll !== null) window.clearInterval(this.poll);
    if (this.saving !== null) window.clearInterval(this.saving);
    this.poll = this.saving = null;
    this.engine.onEntitiesChanged = null;
    void this.savePlayer();
  }

  /** Simulation tick now, interpolated from the last authoritative tick. */
  tickNow(): number {
    return this.tick + (performance.now() - this.tickAt) / 1000;
  }

  private async syncOnce(): Promise<void> {
    const world = this.engine.world;
    if (!this.companion || !world || this.inFlight) return;
    this.inFlight = true;
    const t0 = performance.now();
    try {
      const sync: WorldSync = await this.companion.sync(world.world_id, this.revision);
      const events = sync.events ?? [];
      if (this.engine.world?.world_id !== world.world_id) return;
      this.lastSyncMs = performance.now() - t0;
      this.syncError = null;
      this.tick = sync.tick;
      this.tickAt = performance.now();
      if (events.length || sync.truncated) {
        // re-read the affected regions: authoritative entities, region revisions and neural-asset validity
        const coords = new Map<string, RegionCoord>();
        for (const ev of events) for (const c of (ev.regions ?? []) as RegionCoord[]) coords.set(`${c.rx},${c.rz}`, c);
        await this.engine.reloadRegions(sync.truncated ? null : [...coords.values()]);
        const spreads = events.filter((e) => e.type === "fire_spread" && e.cause === "simulation");
        if (spreads.length) {
          const n = spreads.reduce((k, e) => k + (e.entity_ids?.length ?? 0), 0);
          this.ui.toast(n === 1 ? "The fire spreads to a nearby tree." : `The fire spreads to ${n} more trees.`, "fire");
        }
      }
      this.revision = sync.revision;
    } catch (error) {
      this.syncError = (error as Error).message;
    } finally {
      this.inFlight = false;
    }
  }

  private async savePlayer(): Promise<void> {
    const world = this.engine.world;
    if (!this.companion || !world) return;
    const p = this.engine.player;
    const state = {
      ...world.player,
      position: [p.position.x, p.position.y - 1.7, p.position.z] as [number, number, number],
      yaw: p.yaw,
      pitch: Math.max(-1.5, Math.min(1.5, p.pitch)),
    };
    const key = state.position.map((v) => v.toFixed(1)).join(",");
    if (key === this.lastSaved) return;
    try {
      await this.companion.putPlayer(world.world_id, state);
      this.lastSaved = key;
    } catch {
      /* best effort; retried on the next interval */
    }
  }

  private noticeChanges(before: WorldEntity | undefined, after: WorldEntity): void {
    if (before?.state?.condition === "burning" && after.state?.condition === "charred") {
      this.smoulder.set(after.id, performance.now() / 1000);
    }
  }

  // ---------------------------------------------------------------- per frame

  frame(): void {
    const now = performance.now() / 1000;
    const tick = this.tickNow();
    const updates = new Map<string, DynamicVisual>();
    const sources: FireSource[] = [];
    const cam = this.engine.player.position;
    for (const e of this.engine.loadedEntities()) {
      const c = e.state?.condition;
      if (c !== "burning" && !this.smoulder.has(e.id)) continue;
      const pos = e.transform.position as [number, number, number];
      const scale = e.transform.scale ?? 1;
      const h = Math.max(2, e.bound_radius * 1.25);
      if (c === "burning") {
        const age = tick - (e.state?.burn_started_tick ?? tick);
        const dur = burnDurationTicks(e);
        const progress = Math.max(0, Math.min(1, age / dur));
        const params = (e.geometry_params ?? {}) as Record<string, number>;
        const canopy0 = params.canopy;
        updates.set(e.id, {
          burning: true,
          burnVisual: visualBurn(e, tick),
          // foliage is consumed during the second half of the burn (visual; the server writes the end state)
          params: canopy0 !== undefined ? { canopy: canopy0 * (1 - 0.94 * smooth(0.45, 0.95, progress)) } : undefined,
        });
        const intensity = smooth(0, 6, age) * (1 - 0.85 * smooth(0.8, 1, progress));
        sources.push({ id: e.id, position: pos, height: h, radius: Math.max(1.2, e.bound_radius * 0.38 * Math.min(scale, 1.4)), intensity, progress });
      } else {
        const since = now - (this.smoulder.get(e.id) ?? now);
        if (since > SMOULDER_S) {
          this.smoulder.delete(e.id);
          continue;
        }
        sources.push({ id: e.id, position: pos, height: h * 0.6, radius: 1, intensity: 1 - since / SMOULDER_S, progress: 1, smoulder: true });
      }
    }
    this.engine.near.setDynamic(updates);
    const flicker = (id: string) => 0.8 + 0.2 * Math.sin(now * 13 + id.length * 7.1) * Math.sin(now * 7.3 + id.length);
    const lights = sources
      .filter((s) => !s.smoulder && s.intensity > 0.05)
      .map((s) => ({ x: s.position[0], y: s.position[1] + s.height * 0.45, z: s.position[2], intensity: 9 * s.intensity * flicker(s.id), d: Math.hypot(s.position[0] - cam.x, s.position[2] - cam.z), color: undefined as [number, number, number] | undefined }))
      .sort((a, b) => a.d - b.d);
    // generated objects with emissive materials light their surroundings (nearest ones; fires take precedence)
    const glows: typeof lights = [];
    for (const e of this.engine.loadedEntities()) {
      if (e.state?.condition === "charred" || e.state?.condition === "removed") continue;
      const g = this.engine.near.archetypeGlow(e.geometry_id);
      if (!g) continue;
      const pos = e.transform.position as number[];
      const d = Math.hypot(pos[0] - cam.x, pos[2] - cam.z);
      if (d > 45) continue;
      const peak = Math.max(g[0], g[1], g[2]);
      glows.push({ x: pos[0], y: pos[1] + e.bound_radius * 0.6, z: pos[2], intensity: Math.min(6, peak) * (e.transform.scale ?? 1), d,
        color: [g[0] / peak, g[1] / peak, g[2] / peak] });
    }
    glows.sort((a, b) => a.d - b.d);
    this.engine.near.setFireLights([...lights, ...glows].slice(0, 8));
    this.engine.setFireSources(sources);
    this.updatePrompt();
  }

  private updatePrompt(): void {
    const hit = this.engine.hoveredHit();
    if (!hit) {
      this.prompt = null;
      return;
    }
    const { entity: e, distance } = hit;
    const reach = ACTION_RANGE_M + Math.min(e.bound_radius, 3) * 0.5;
    const flammable = (e.capabilities ?? []).includes("flammable");
    const c = e.state?.condition ?? "intact";
    const name = e.kind === "tree" ? "tree" : e.kind;
    if (c === "burning") this.prompt = { entityId: e.id, kind: e.kind, label: `The ${name} is burning`, enabled: false };
    else if (c === "charred") this.prompt = { entityId: e.id, kind: e.kind, label: `A charred ${name}`, enabled: false };
    else if (!flammable) this.prompt = null;
    else if (distance > reach) this.prompt = { entityId: e.id, kind: e.kind, label: "E — Burn it?", enabled: false, hint: "Move closer" };
    else this.prompt = { entityId: e.id, kind: e.kind, label: "E — Burn it?", enabled: !this.busy, hint: this.companion ? undefined : "offline sample: read-only" };
  }

  /** E key: ask the server to ignite the entity under the crosshair. */
  async interact(): Promise<void> {
    const world = this.engine.world;
    const prompt = this.prompt;
    if (!world || !prompt || !prompt.enabled || this.busy) return;
    if (!this.companion) {
      this.ui.toast("This is the read-only offline sample. Generate or open a saved world to change it.", "error");
      return;
    }
    this.busy = true;
    const p = this.engine.player;
    const f = p.forward();
    try {
      const result: MutationResult = await this.companion.command({
        command_id: crypto.randomUUID(),
        world_id: world.world_id,
        player_id: world.player.player_id,
        text: "Burn it",
        action: "ignite",
        target_hint: prompt.entityId,
        player_position: [p.position.x, p.position.y - 1.0, p.position.z],
        view_direction: [f.x, f.y, f.z],
        expected_world_revision: this.revision,
      });
      if (result.status === "committed") {
        this.engine.applyEntities(result.entities ?? []);
        this.revision = Math.max(this.revision, result.world_revision);
        this.ui.toast(result.message || "It catches fire.", "fire");
        void this.syncOnce();
      } else {
        this.ui.toast(result.message, "error");
      }
    } catch (error) {
      this.ui.toast(`Could not reach the world server: ${(error as Error).message}`, "error");
    } finally {
      this.busy = false;
    }
  }
}

function smooth(a: number, b: number, x: number): number {
  const t = Math.max(0, Math.min(1, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
}
