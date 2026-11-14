import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { GeometryDefinition, WorldEntity } from "@contracts";
import { ApiFailure, Companion, MIN_RENDERABLE_GENERATOR_VERSION, loadOfflineSample, readPairing, type WorldSummary } from "../app/api";
import { Engine, type EngineStatus } from "../app/Engine";
import { DEBUG_VIEWS, type DebugView, type QualityName } from "../rendering/hybrid/types";

type Backend =
  | { state: "checking" }
  | { state: "connected"; database: string; contract: string }
  | { state: "unauthorized" }
  | { state: "unreachable"; message: string };

const QUALITIES: QualityName[] = ["ultra", "high", "balanced", "performance"];

declare global {
  interface Window {
    __greality?: { engine: Engine; status: () => EngineStatus; ready: boolean; error: string | null };
  }
}

function fmt(n: number | null | undefined, digits = 1): string {
  return n === null || n === undefined || Number.isNaN(n) ? "n/a" : n.toFixed(digits);
}

export function App() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const engineRef = useRef<Engine | null>(null);
  const companion = useMemo(() => new Companion(readPairing()), []);
  const [backend, setBackend] = useState<Backend>({ state: "checking" });
  const [worlds, setWorlds] = useState<WorldSummary[]>([]);
  const [status, setStatus] = useState<EngineStatus | null>(null);
  const [source, setSource] = useState<"none" | "live" | "offline">("none");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [showDiag, setShowDiag] = useState(true);
  const [showPanel, setShowPanel] = useState(true);
  const [fog, setFog] = useState<number | null>(null);
  const [hour, setHour] = useState<number | null>(null);
  const [pinned, setPinned] = useState<WorldEntity | null>(null);
  // local parameter previews, keyed by entity id so one entity's sliders never leak onto another
  const [previews, setPreviews] = useState<Record<string, Record<string, number>>>({});
  const [notes, setNotes] = useState<string[]>([]);
  const [legacy, setLegacy] = useState(0);

  const openOffline = useCallback(async () => {
    const engine = engineRef.current;
    if (!engine) return;
    setBusy("Loading offline sample…");
    setError(null);
    try {
      const sample = await loadOfflineSample();
      await engine.openWorld(sample.world, sample.regions, null);
      setPinned(null);
      setPreviews({});
      setSource("offline");
      setNotes((sample.world.spec.interpretation_notes ?? []) as string[]);
      setFog(null);
      setHour(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }, []);

  const openLive = useCallback(
    async (worldId: string) => {
      const engine = engineRef.current;
      if (!engine) return;
      setBusy("Loading world from the companion…");
      setError(null);
      try {
        const world = await companion.getWorld(worldId);
        await engine.openWorld(world, null, companion);
        setPinned(null);
        setPreviews({});
        setSource("live");
        setNotes((world.spec.interpretation_notes ?? []) as string[]);
        setFog(null);
        setHour(null);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(null);
      }
    },
    [companion],
  );

  const refreshBackend = useCallback(async () => {
    try {
      const health = await companion.health();
      try {
        const all = await companion.listWorlds();
        const list = all.filter((w) => w.generator_version >= MIN_RENDERABLE_GENERATOR_VERSION);
        setLegacy(all.length - list.length);
        setWorlds(list);
        setBackend({ state: "connected", database: health.database, contract: health.contract_version ?? "?" });
        return list;
      } catch (e) {
        if (e instanceof ApiFailure && e.status === 401) setBackend({ state: "unauthorized" });
        else setBackend({ state: "unreachable", message: (e as Error).message });
      }
    } catch (e) {
      setBackend({ state: "unreachable", message: (e as Error).message });
    }
    return null;
  }, [companion]);

  useEffect(() => {
    const canvas = canvasRef.current!;
    let engine: Engine;
    try {
      engine = new Engine(canvas);
    } catch (e) {
      setFatal((e as Error).message);
      window.__greality = { engine: null as unknown as Engine, status: () => null as unknown as EngineStatus, ready: false, error: (e as Error).message };
      return;
    }
    engineRef.current = engine;
    engine.onStatus = setStatus;
    engine.start();
    const hook = { engine, status: () => engine.status(), ready: false, error: null as string | null };
    window.__greality = hook;

    const params = new URLSearchParams(window.location.search);
    const q = params.get("q") as QualityName | null;
    if (q && QUALITIES.includes(q)) engine.setQuality(q);
    const view = params.get("debug") as DebugView | null;
    if (view && DEBUG_VIEWS.includes(view)) engine.setDebugView(view);

    (async () => {
      const list = params.get("sample") === "1" ? null : await refreshBackend();
      const wanted = params.get("world");
      if (list && (wanted || list.length)) await openLive(wanted ?? list[0].world_id);
      else await openOffline();
      const pose = params.get("pose")?.split(",").map(Number);
      if (pose && pose.length >= 5 && pose.every(Number.isFinite)) {
        engine.player.flying = true;
        engine.player.position.set(pose[0], pose[1], pose[2]);
        engine.player.yaw = pose[3];
        engine.player.pitch = pose[4];
      }
      hook.ready = true;
    })().catch((e) => {
      hook.error = (e as Error).message;
      setError((e as Error).message);
    });

    const onKey = (e: KeyboardEvent) => {
      if (e.code === "F3") {
        e.preventDefault();
        setShowDiag((v) => !v);
      }
      if (e.code === "Tab") {
        e.preventDefault();
        setShowPanel((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      engine.dispose();
      engineRef.current = null;
    };
  }, [openLive, openOffline, refreshBackend]);

  const createWorld = async () => {
    if (!prompt.trim()) return;
    setBusy("Asking the language model to design the world…");
    setError(null);
    try {
      const world = await companion.createWorld(prompt.trim());
      setBusy(null);
      await refreshBackend();
      await openLive(world.world_id);
    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
    }
  };

  const engine = engineRef.current;
  const hovered = status?.hovered?.entity ?? null;
  const inspected = pinned ?? hovered;
  const preview = (inspected && previews[inspected.id]) || {};
  const def: GeometryDefinition | undefined = inspected && engine ? engine.near.archetypeDef(inspected.geometry_id) : undefined;

  useEffect(() => {
    // pin / unpin the entity under the crosshair with the left mouse button while the pointer is locked
    const onDown = (e: MouseEvent) => {
      if (!document.pointerLockElement || e.button !== 0) return;
      const current = engineRef.current?.status().hovered?.entity ?? null;
      setPinned((prev) => (prev && (!current || current.id === prev.id) ? null : current));
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, []);

  const setParam = (name: string, value: number) => {
    if (!inspected || !engine) return;
    const next = { ...(previews[inspected.id] ?? {}), [name]: value };
    setPreviews({ ...previews, [inspected.id]: next });
    engine.near.setPreviewParams(inspected.id, next);
  };
  const resetPreview = () => {
    engine?.near.clearPreviews();
    setPreviews({});
  };

  return (
    <div className="app">
      <canvas ref={canvasRef} className="viewport" />
      <div className="crosshair" />

      {fatal && <div className="modal"><h2>Cannot start the renderer</h2><p>{fatal}</p></div>}

      <div className="topbar">
        <span className="brand">G-reality</span>
        <span className={`chip ${backend.state}`}>
          {backend.state === "checking" && "companion: checking…"}
          {backend.state === "connected" && `companion: connected · Neon ${backend.database}`}
          {backend.state === "unauthorized" && "companion: needs pairing token"}
          {backend.state === "unreachable" && "companion: offline"}
        </span>
        <span className={`chip ${source}`}>
          {source === "live" && `live world · rev ${status?.worldRevision ?? "?"}`}
          {source === "offline" && "offline sample (read-only snapshot)"}
          {source === "none" && "no world"}
        </span>
        <span className="chip far">far field: {status?.farField ?? "…"}</span>
        {busy && <span className="chip busy">{busy}</span>}
        {error && <span className="chip error" title={error}>{error}</span>}
      </div>

      {showPanel && (
        <div className="panel left">
          <h3>World</h3>
          {backend.state === "connected" ? (
            <>
              <select value={source === "live" ? (status?.worldId ?? "") : ""} onChange={(e) => e.target.value && void openLive(e.target.value)}>
                <option value="">— open a saved world —</option>
                {worlds.map((w) => (
                  <option key={w.world_id} value={w.world_id}>{w.name} · rev {w.revision}</option>
                ))}
              </select>
              {legacy > 0 && (
                <div className="hint small">{legacy} older world(s) hidden: they were generated with the previous terrain function and would not line up with the current one.</div>
              )}
              <textarea rows={3} placeholder="Describe a world: a misty pine forest around a lake with a village on the eastern shore"
                value={prompt} onChange={(e) => setPrompt(e.target.value)} />
              <button disabled={!!busy || !prompt.trim()} onClick={() => void createWorld()}>Generate world</button>
            </>
          ) : (
            <div className="hint">
              {backend.state === "unauthorized"
                ? "The companion is running but this tab has no pairing token. Open the “open:” link printed in the companion console."
                : "Start the companion to load or generate persistent worlds:"}
              <code>powershell -ExecutionPolicy Bypass -File scripts\start-companion.ps1</code>
              <button onClick={() => void refreshBackend()}>Retry</button>
            </div>
          )}
          <button className="secondary" disabled={!!busy} onClick={() => void openOffline()}>Open offline sample</button>
          {notes.length > 0 && (
            <ul className="notes">{notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
          )}

          <h3>Rendering</h3>
          <label>Quality
            <select value={status?.quality ?? "high"} onChange={(e) => engine?.setQuality(e.target.value as QualityName)}>
              {QUALITIES.map((q) => <option key={q}>{q}</option>)}
            </select>
          </label>
          <label>Debug view
            <select value={status?.debugView ?? "final"} onChange={(e) => engine?.setDebugView(e.target.value as DebugView)}>
              {DEBUG_VIEWS.map((v) => <option key={v}>{v}</option>)}
            </select>
          </label>
          <label>Fog density {fmt(fog ?? engine?.world?.environment.fog_density ?? 0, 4)}
            <input type="range" min={0} max={0.03} step={0.0005} value={fog ?? engine?.world?.environment.fog_density ?? 0}
              onChange={(e) => { const v = Number(e.target.value); setFog(v); engine?.near.setEnvironment({ fogDensity: v }); }} />
          </label>
          <label>Time of day {fmt(hour ?? engine?.world?.environment.time_of_day_h ?? 10, 1)} h
            <input type="range" min={5} max={19} step={0.1} value={hour ?? engine?.world?.environment.time_of_day_h ?? 10}
              onChange={(e) => { const v = Number(e.target.value); setHour(v); engine?.near.setEnvironment({ timeOfDay: v }); }} />
          </label>
          <div className="hint small">Fog and time sliders are local previews of the render; the stored world is unchanged.</div>

          <h3>Controls</h3>
          <div className="hint small">
            Click the view to capture the mouse · WASD move · Shift sprint · Space jump · F fly (Space/C up/down) ·
            click an object to pin it in the inspector · F3 diagnostics · Tab panels · Esc release mouse
          </div>
        </div>
      )}

      {showPanel && inspected && def && (
        <div className="panel right">
          <h3>Inspector {pinned ? "(pinned)" : "(crosshair)"}</h3>
          <table>
            <tbody>
              <tr><td>entity</td><td>{inspected.id}</td></tr>
              <tr><td>kind</td><td>{inspected.kind} · {inspected.geometry_id}</td></tr>
              <tr><td>condition</td><td>{inspected.state?.condition ?? "intact"}</td></tr>
              <tr><td>entity revision</td><td>{inspected.revision}</td></tr>
              <tr><td>distance</td><td>{fmt(status?.hovered?.distance, 1)} m</td></tr>
              <tr><td>capabilities</td><td>{(inspected.capabilities ?? []).join(", ") || "none"}</td></tr>
              <tr><td>SDF nodes</td><td>{countNodes(def.root)} ({opsUsed(def.root)})</td></tr>
            </tbody>
          </table>
          {Object.entries(def.parameters ?? {}).map(([name, spec]) => {
            const stored = (inspected.geometry_params as Record<string, number> | undefined)?.[name] ?? spec.default;
            const value = preview[name] ?? stored;
            return (
              <label key={name}>{name} = {fmt(value, 2)} {preview[name] !== undefined && <em>(preview)</em>}
                <input type="range" min={spec.min} max={spec.max} step={(spec.max - spec.min) / 100} value={value}
                  onChange={(e) => setParam(name, Number(e.target.value))} />
              </label>
            );
          })}
          {Object.keys(def.parameters ?? {}).length === 0 && <div className="hint small">This archetype has no parameters.</div>}
          <button className="secondary" onClick={resetPreview} disabled={!status?.previews}>Reset previews ({status?.previews ?? 0})</button>
          <div className="hint small">
            Sliders change the mathematical definition evaluated by the shader for this entity only, on this
            screen only. They are not saved; committed changes go through world commands (Chunk 5).
          </div>
        </div>
      )}

      {showDiag && status && (
        <div className="diag">
          <div><b>{fmt(status.fps, 0)} fps</b> · frame {fmt(status.frameMs.avg)} ms (p50 {fmt(status.frameMs.p50)}, p95 {fmt(status.frameMs.p95)}, max {fmt(status.frameMs.max)})</div>
          <div>near-field SDF pass (GPU): {status.nearGpuMs === null ? "timer unavailable" : `${fmt(status.nearGpuMs, 2)} ms`}</div>
          <div>internal {status.internalSize.join("x")} → canvas {status.canvasSize.join("x")} · {status.quality} · view {status.debugView}</div>
          <div>SDF instances {status.instances} · archetypes {status.archetypes} · materials {status.materials}</div>
          <div>grid {status.grid}{status.gridOverflow ? ` · OVERFLOW ${status.gridOverflow}` : ""}</div>
          <div>shader {Math.round(status.shaderChars / 1024)} KiB, compiled in {fmt(status.compileMs, 0)} ms{status.compileErrors.length ? " · SHADER ERRORS" : ""}</div>
          <div>GPU↔CPU terrain max error: {status.terrainParityM === null ? "n/a" : `${(status.terrainParityM * 1000).toFixed(2)} mm`}</div>
          <div>regions loaded {status.regionsLoaded} · pending {status.regionsPending} · world rev {status.worldRevision ?? "–"} · region ({status.region.join(",")}) rev {status.regionRevision ?? "–"}</div>
          <div>camera {status.position.map((v) => v.toFixed(1)).join(", ")}{status.flying ? " · flying" : ""}</div>
          <div>far field (Gaussian): {status.farField} · splats 0</div>
          <div className="gpu">{status.gpuRenderer}</div>
          {status.loadError && <div className="err">region load: {status.loadError}</div>}
          {status.compileErrors.map((e, i) => <pre key={i} className="err">{e.slice(0, 600)}</pre>)}
        </div>
      )}

      {status && !status.pointerLocked && source !== "none" && !busy && (
        <div className="center-hint">Click to look around</div>
      )}
    </div>
  );
}

function countNodes(node: { children?: unknown[] | null }): number {
  return 1 + ((node.children ?? []) as { children?: unknown[] }[]).reduce((n, c) => n + countNodes(c), 0);
}

function opsUsed(node: { op: string; children?: unknown[] | null }): string {
  const set = new Set<string>();
  const walk = (n: { op: string; children?: unknown[] | null }) => {
    set.add(n.op);
    ((n.children ?? []) as { op: string; children?: unknown[] }[]).forEach(walk);
  };
  walk(node);
  return [...set].join(", ");
}
