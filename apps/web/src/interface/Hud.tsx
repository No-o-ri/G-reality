/** In-game player interface: crosshair, interaction prompt, messages, and the pause menu. */
import type { InteractionPrompt } from "../app/Gameplay";

export interface Toast {
  id: number;
  message: string;
  tone: "info" | "fire" | "error";
}

interface Props {
  worldName: string;
  live: boolean;
  interpreter: string | null;
  prompt: InteractionPrompt | null;
  toasts: Toast[];
  paused: boolean;
  locomotion: string;
  onResume(): void;
  onExit(): void;
  devMode: boolean;
  onToggleDev(): void;
}

export function Hud({ worldName, live, interpreter, prompt, toasts, paused, locomotion, onResume, onExit, devMode, onToggleDev }: Props) {
  return (
    <>
      <div className={`crosshair ${prompt?.enabled ? "active" : ""}`} />
      {prompt && !paused && (
        <div className={`interact ${prompt.enabled ? "" : "disabled"}`}>
          {prompt.label.startsWith("E") ? (
            <>
              <span className="key">E</span>
              <span>{prompt.label.slice(4)}</span>
            </>
          ) : (
            <span>{prompt.label}</span>
          )}
          {prompt.hint && <span className="hint small">{prompt.hint}</span>}
        </div>
      )}
      <div className="toasts">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.tone}`}>{t.message}</div>
        ))}
      </div>
      <div className="world-title">
        <span>{worldName}</span>
        {!live && <span className="tag">offline sample · read-only</span>}
        {live && interpreter && interpreter !== "gemini" && interpreter !== "llm" && <span className="tag">{interpreter}</span>}
        {locomotion === "swimming" && <span className="tag water">swimming</span>}
      </div>
      {paused && (
        <div className="pause">
          <div className="pause-card">
            <h2>{worldName}</h2>
            <button className="primary" onClick={onResume}>Resume</button>
            <button className="secondary" onClick={onExit}>Back to the menu</button>
            <div className="controls">
              <div><b>WASD</b> move · <b>Shift</b> sprint · <b>Space</b> jump / swim up</div>
              <div><b>Mouse</b> look · <b>E</b> interact · <b>Esc</b> release mouse</div>
            </div>
            <label className="dev-toggle">
              <input type="checkbox" checked={devMode} onChange={onToggleDev} /> Developer tools (diagnostics, fly camera, neural far field)
            </label>
          </div>
        </div>
      )}
    </>
  );
}
