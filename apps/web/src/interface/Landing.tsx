/**
 * Landing page: describe a world, generate it through the companion (Gemini, or the offline keyword interpreter when
 * asked or when no key is configured), or reopen a saved world. The live engine renders the offline sample behind it.
 */
import { useState } from "react";
import type { WorldSummary } from "../app/api";

export type BackendState =
  | { state: "checking" }
  | { state: "connected"; database: string; contract: string }
  | { state: "unauthorized" }
  | { state: "unreachable"; message: string };

export interface GenerationProgress {
  stage: string;
  startedAt: number;
}

export interface GenerationError {
  message: string;
  canUseOffline: boolean;
}

const EXAMPLES = [
  "A dense alpine forest of tall pines around a cold blue lake, snowy mountains behind, a small wooden settlement on the shore",
  "A dry red canyon with layered mesas and towering rock spires, sparse cacti and dead trees, harsh afternoon sun",
  "A foggy volcanic island coast at dusk: black rock, a smoking crater, twisted wind-bent trees and a few stilt huts by the sea",
  "Golden autumn birch woods on gentle hills, a stone village, low morning sun and light mist",
];

interface Props {
  backend: BackendState;
  worlds: WorldSummary[];
  progress: GenerationProgress | null;
  error: GenerationError | null;
  onGenerate(prompt: string, offline: boolean): void;
  onOpen(worldId: string): void;
  onSample(): void;
  onRetryBackend(): void;
  now: number;
}

export function Landing({ backend, worlds, progress, error, onGenerate, onOpen, onSample, onRetryBackend, now }: Props) {
  const [prompt, setPrompt] = useState("");
  const connected = backend.state === "connected";
  const busy = progress !== null;
  const submit = (offline = false) => {
    if (prompt.trim().length >= 3 && connected && !busy) onGenerate(prompt.trim(), offline);
  };

  return (
    <div className="landing">
      <div className="landing-inner">
        <div className="wordmark">G-REALITY</div>
        <h1>Describe a world. Walk into it.</h1>
        <p className="tagline">
          Terrain, forests, rocks and buildings are generated as new geometry from your description, and every
          change you make is kept by the world.
        </p>

        <div className={`prompt-box ${busy ? "busy" : ""}`}>
          <textarea
            rows={3}
            maxLength={2000}
            placeholder="A misty pine forest around a lake, with a wooden village on the eastern shore…"
            value={prompt}
            disabled={busy}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit();
            }}
          />
          <div className="prompt-actions">
            <span className="hint small">{prompt.length > 0 ? `${prompt.length}/2000 · Ctrl+Enter` : "Try an example below"}</span>
            <button className="primary" disabled={!connected || busy || prompt.trim().length < 3} onClick={() => submit()}>
              {busy ? "Generating…" : "Generate world"}
            </button>
          </div>
        </div>

        {!busy && (
          <div className="examples">
            {EXAMPLES.map((ex) => (
              <button key={ex} className="example" onClick={() => setPrompt(ex)} title={ex}>
                {ex.length > 70 ? ex.slice(0, 68) + "…" : ex}
              </button>
            ))}
          </div>
        )}

        {busy && progress && (
          <div className="progress">
            <div className="spinner" />
            <div>
              <div>{progress.stage}</div>
              <div className="hint small">{Math.floor((now - progress.startedAt) / 1000)} s</div>
            </div>
          </div>
        )}

        {error && (
          <div className="error-box">
            <div>{error.message}</div>
            <div className="row">
              <button onClick={() => submit(false)} disabled={busy}>Try again</button>
              {error.canUseOffline && (
                <button className="secondary" onClick={() => submit(true)} disabled={busy}>
                  Generate with the offline interpreter
                </button>
              )}
            </div>
            {error.canUseOffline && (
              <div className="hint small">The offline interpreter reads keywords only: worlds are still generated, but follow the description less closely.</div>
            )}
          </div>
        )}

        <div className="status-line">
          {backend.state === "checking" && <span>Connecting to the G-reality server…</span>}
          {backend.state === "connected" && <span className="ok">● World server connected · Neon {backend.database}</span>}
          {backend.state === "unauthorized" && (
            <span className="warn">This page is not paired with the server. Open G-reality from its launcher (G-reality.cmd), which opens the paired link.</span>
          )}
          {backend.state === "unreachable" && (
            <span className="warn">
              The G-reality server is not running. Start it with G-reality.cmd (or scripts\start-companion.ps1 in development).{" "}
              <button className="link" onClick={onRetryBackend}>Retry</button>
            </span>
          )}
        </div>

        {worlds.length > 0 && (
          <div className="saved">
            <h3>Your worlds</h3>
            <div className="world-list">
              {worlds.slice(0, 12).map((w) => (
                <button key={w.world_id} className="world-card" disabled={busy} onClick={() => onOpen(w.world_id)}>
                  <span className="name">{w.name}</span>
                  <span className="meta">{new Date(w.created_at).toLocaleString()} · revision {w.revision}</span>
                </button>
              ))}
            </div>
          </div>
        )}
        <button className="link sample" onClick={onSample} disabled={busy}>Explore the offline sample world (read-only)</button>
      </div>
    </div>
  );
}
