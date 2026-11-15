/**
 * First-person controller: pointer-lock mouse look, WASD, sprint, jump, gravity, terrain following, collision with
 * solid world objects, a walkable-slope limit, wading and swimming. Flying is a developer-only mode.
 */
import * as THREE from "three";

export const EYE_HEIGHT = 1.7;
export const BODY_RADIUS = 0.35;
const GRAVITY = 18;
const JUMP_SPEED = 5.6;
const MAX_WALK_SLOPE = 1.05; // rise over run (~46°): steeper ground cannot be walked up
const SWIM_DEPTH = 1.35; // water deeper than this at the feet: swim
const WADE_DEPTH = 0.35;

/** Pushes a body (feet position, radius, height) out of solid objects; returns the corrected XZ. */
export type Collider = (x: number, feetY: number, z: number, radius: number, height: number) => [number, number];

export type Locomotion = "walking" | "wading" | "swimming" | "flying" | "falling";

export class Player {
  readonly position = new THREE.Vector3();
  yaw = 0; // radians, 0 looks down -Z, positive turns left
  pitch = 0;
  flying = false;
  /** developer mode: allows F to toggle flying */
  allowFly = false;
  locked = false;
  speed = 0;
  locomotion: Locomotion = "walking";
  /** water surface height at the player while in water (for ripples), else null */
  waterContact: number | null = null;

  private keys = new Set<string>();
  private verticalVelocity = 0;
  private bob = 0;
  private readonly cleanup: Array<() => void> = [];
  private grounded = true;

  constructor(private readonly element: HTMLElement) {
    const on = <K extends keyof DocumentEventMap>(target: Document | HTMLElement, type: K, fn: (e: DocumentEventMap[K]) => void) => {
      target.addEventListener(type, fn as EventListener);
      this.cleanup.push(() => target.removeEventListener(type, fn as EventListener));
    };
    on(document, "keydown", (e) => {
      if ((e.target as HTMLElement | null)?.closest?.("input, textarea, select")) return;
      this.keys.add(e.code);
      if (e.code === "KeyF" && this.allowFly) this.flying = !this.flying;
      if (e.code === "Space" && this.locked) e.preventDefault();
    });
    on(document, "keyup", (e) => this.keys.delete(e.code));
    on(element, "click", () => this.lock());
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

  lock(): void {
    if (!this.locked) void this.element.requestPointerLock?.();
  }

  forward(out = new THREE.Vector3()): THREE.Vector3 {
    const cp = Math.cos(this.pitch);
    return out.set(-Math.sin(this.yaw) * cp, Math.sin(this.pitch), -Math.cos(this.yaw) * cp);
  }

  update(dt: number, groundHeight: (x: number, z: number) => number, waterLevel: number | null, collide: Collider | null): void {
    const k = this.keys;
    const f = (k.has("KeyW") ? 1 : 0) - (k.has("KeyS") ? 1 : 0);
    const s = (k.has("KeyD") ? 1 : 0) - (k.has("KeyA") ? 1 : 0);
    const sprint = k.has("ShiftLeft") || k.has("ShiftRight");
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

    if (this.flying) {
      const speed = 22 * (sprint ? 5 : 1);
      const up = (k.has("Space") ? 1 : 0) - (k.has("ControlLeft") || k.has("KeyC") ? 1 : 0);
      this.position.x += dx * speed * dt;
      this.position.z += dz * speed * dt;
      this.position.y += (up + f * Math.sin(this.pitch)) * speed * dt;
      const floor = Math.max(groundHeight(this.position.x, this.position.z), waterLevel ?? -Infinity) + 0.4;
      this.position.y = Math.max(this.position.y, floor);
      this.verticalVelocity = 0;
      this.locomotion = "flying";
      this.waterContact = null;
      this.speed = before.distanceTo(this.position) / Math.max(dt, 1e-4);
      return;
    }

    const feetBefore = this.position.y - EYE_HEIGHT;
    const groundHere = groundHeight(this.position.x, this.position.z);
    const depth = waterLevel === null ? 0 : waterLevel - groundHere;
    const swimming = depth > SWIM_DEPTH && feetBefore < waterLevel! - 0.4;
    const wading = !swimming && depth > WADE_DEPTH;
    let speed = 5.2 * (sprint ? 1.9 : 1);
    if (wading) speed *= 0.55;
    if (swimming) speed = 2.4 * (sprint ? 1.5 : 1);

    let nx = this.position.x + dx * speed * dt;
    let nz = this.position.z + dz * speed * dt;
    // walkable-slope limit: refuse to climb ground steeper than MAX_WALK_SLOPE (sliding along it instead)
    if (!swimming && len > 0) {
      const step = Math.hypot(nx - this.position.x, nz - this.position.z);
      const rise = groundHeight(nx, nz) - groundHere;
      if (step > 1e-4 && rise / step > MAX_WALK_SLOPE && this.grounded) {
        const gx = groundHeight(this.position.x + 0.5, this.position.z) - groundHeight(this.position.x - 0.5, this.position.z);
        const gz = groundHeight(this.position.x, this.position.z + 0.5) - groundHeight(this.position.x, this.position.z - 0.5);
        const gl = Math.hypot(gx, gz) || 1;
        const along = (nx - this.position.x) * (-gz / gl) + (nz - this.position.z) * (gx / gl); // move along the contour
        nx = this.position.x + along * (-gz / gl);
        nz = this.position.z + along * (gx / gl);
      }
    }
    if (collide) [nx, nz] = collide(nx, this.position.y - EYE_HEIGHT, nz, BODY_RADIUS, EYE_HEIGHT + 0.15);
    this.position.x = nx;
    this.position.z = nz;

    const ground = groundHeight(nx, nz);
    if (swimming) {
      // buoyancy: float with the head above the surface, bob gently; Space swims up / climbs out at the shore
      const target = waterLevel! - 1.15 + EYE_HEIGHT;
      this.bob += dt * 2.1;
      this.verticalVelocity += (target + Math.sin(this.bob) * 0.06 - this.position.y) * 6 * dt;
      this.verticalVelocity *= Math.exp(-3.5 * dt);
      if (k.has("Space")) this.verticalVelocity += 6 * dt;
      this.position.y += this.verticalVelocity * dt;
      this.position.y = Math.max(this.position.y, ground + EYE_HEIGHT * 0.6);
      this.locomotion = "swimming";
      this.grounded = false;
    } else {
      const floor = ground + EYE_HEIGHT;
      if (k.has("Space") && this.grounded) {
        this.verticalVelocity = JUMP_SPEED * (wading ? 0.6 : 1);
        this.grounded = false;
      }
      this.verticalVelocity -= GRAVITY * dt;
      this.position.y += this.verticalVelocity * dt;
      // stick to the ground when walking downhill instead of hopping off every crest
      if (this.grounded && this.position.y > floor && this.position.y - floor < 0.45 && this.verticalVelocity <= 0) {
        this.position.y = floor;
      }
      if (this.position.y <= floor) {
        this.position.y = floor;
        this.verticalVelocity = 0;
        this.grounded = true;
      } else if (this.position.y > floor + 0.5) {
        this.grounded = false;
      }
      this.locomotion = this.grounded ? (wading ? "wading" : "walking") : "falling";
    }
    this.waterContact = waterLevel !== null && depth > 0.05 ? waterLevel : null;
    this.speed = Math.hypot(this.position.x - before.x, this.position.z - before.z) / Math.max(dt, 1e-4);
  }

  apply(camera: THREE.PerspectiveCamera): void {
    camera.position.copy(this.position);
    camera.rotation.set(this.pitch, this.yaw, 0, "YXZ");
  }

  dispose(): void {
    this.cleanup.forEach((fn) => fn());
  }
}
