/** First-person controller: pointer-lock mouse look, WASD, sprint, walk (terrain following) or fly. */
import * as THREE from "three";

export const EYE_HEIGHT = 1.7;

export class Player {
  readonly position = new THREE.Vector3();
  yaw = 0; // radians, 0 looks down -Z, positive turns left
  pitch = 0;
  flying = false;
  locked = false;
  speed = 0;

  private keys = new Set<string>();
  private verticalVelocity = 0;
  private readonly cleanup: Array<() => void> = [];

  constructor(private readonly element: HTMLElement) {
    const on = <K extends keyof DocumentEventMap>(target: Document | HTMLElement, type: K, fn: (e: DocumentEventMap[K]) => void) => {
      target.addEventListener(type, fn as EventListener);
      this.cleanup.push(() => target.removeEventListener(type, fn as EventListener));
    };
    on(document, "keydown", (e) => {
      if ((e.target as HTMLElement | null)?.closest?.("input, textarea, select")) return;
      this.keys.add(e.code);
      if (e.code === "KeyF") this.flying = !this.flying;
      if (e.code === "Space") e.preventDefault();
    });
    on(document, "keyup", (e) => this.keys.delete(e.code));
    on(element, "click", () => {
      if (!this.locked) void this.element.requestPointerLock?.();
    });
    on(document, "pointerlockchange", () => {
      this.locked = document.pointerLockElement === this.element;
      if (!this.locked) this.keys.clear();
    });
    on(document, "mousemove", (e) => {
      if (!this.locked) return;
      this.yaw -= e.movementX * 0.0022;
      this.pitch = Math.max(-1.5, Math.min(1.5, this.pitch - e.movementY * 0.0022));
    });
  }

  forward(out = new THREE.Vector3()): THREE.Vector3 {
    const cp = Math.cos(this.pitch);
    return out.set(-Math.sin(this.yaw) * cp, Math.sin(this.pitch), -Math.cos(this.yaw) * cp);
  }

  update(dt: number, groundHeight: (x: number, z: number) => number, waterLevel: number | null): void {
    const k = this.keys;
    const f = (k.has("KeyW") ? 1 : 0) - (k.has("KeyS") ? 1 : 0);
    const s = (k.has("KeyD") ? 1 : 0) - (k.has("KeyA") ? 1 : 0);
    const sprint = k.has("ShiftLeft") || k.has("ShiftRight");
    const base = this.flying ? 22 : 5.5;
    const speed = base * (sprint ? (this.flying ? 5 : 2.2) : 1);
    const sin = Math.sin(this.yaw);
    const cos = Math.cos(this.yaw);
    let dx = -sin * f + cos * s;
    let dz = -cos * f - sin * s;
    const len = Math.hypot(dx, dz);
    if (len > 0) {
      dx /= len;
      dz /= len;
    }
    const before = this.position.clone();
    this.position.x += dx * speed * dt;
    this.position.z += dz * speed * dt;

    const floor = Math.max(groundHeight(this.position.x, this.position.z), waterLevel ?? -Infinity) + EYE_HEIGHT;
    if (this.flying) {
      const up = (k.has("Space") ? 1 : 0) - (k.has("ControlLeft") || k.has("KeyC") ? 1 : 0);
      this.position.y += (up + f * Math.sin(this.pitch)) * speed * dt;
      this.position.y = Math.max(this.position.y, floor - EYE_HEIGHT + 0.4);
      this.verticalVelocity = 0;
    } else {
      if (k.has("Space") && this.position.y <= floor + 0.05) this.verticalVelocity = 5.2;
      this.verticalVelocity -= 14 * dt;
      this.position.y += this.verticalVelocity * dt;
      if (this.position.y <= floor) {
        this.position.y = floor;
        this.verticalVelocity = 0;
      }
    }
    this.speed = before.distanceTo(this.position) / Math.max(dt, 1e-4);
  }

  apply(camera: THREE.PerspectiveCamera): void {
    camera.position.copy(this.position);
    camera.rotation.set(this.pitch, this.yaw, 0, "YXZ");
  }

  dispose(): void {
    this.cleanup.forEach((fn) => fn());
  }
}
