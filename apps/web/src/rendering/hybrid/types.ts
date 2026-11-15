/**
 * Renderer-independent boundaries of the hybrid pipeline (docs/architecture/ARCHITECTURE.md §6, §12).
 *
 * Frame order inside one THREE.WebGLRenderer / one camera / one depth buffer:
 *   A. NearFieldRenderer.renderToTarget()  – SDF ray march → color + depth at the internal resolution
 *   B. composite quad (part of `scene`)     – writes color and gl_FragDepth to the canvas
 *   C. FarFieldRenderer objects in `scene`  – Gaussian splats, depth-tested against B (Chunk 3/4)
 */
import type * as THREE from "three";
import type { NeuralAssetManifest, RegionCoord, WorldEntity, WorldState } from "@contracts";

export type QualityName = "ultra" | "high" | "balanced" | "performance";

export interface QualityProfile {
  internalScale: number;
  maxSteps: number;
  shadowSteps: number;
  farDistance: number;
}

export const QUALITY: Record<QualityName, QualityProfile> = {
  ultra: { internalScale: 1.0, maxSteps: 192, shadowSteps: 40, farDistance: 1400 },
  high: { internalScale: 0.8, maxSteps: 144, shadowSteps: 32, farDistance: 1200 },
  balanced: { internalScale: 0.66, maxSteps: 112, shadowSteps: 24, farDistance: 1000 },
  performance: { internalScale: 0.5, maxSteps: 80, shadowSteps: 0, farDistance: 800 },
};

export type DebugView = "final" | "normals" | "steps" | "depth" | "materials" | "instance-cost";
export const DEBUG_VIEWS: DebugView[] = ["final", "normals", "steps", "depth", "materials", "instance-cost"];

export interface NearFieldStats {
  instances: number;
  archetypes: number;
  materials: number;
  gridDim: [number, number];
  gridCell: number;
  gridOverflow: number;
  gridMaxPerCell: number;
  skippedEntities: number;
  internalSize: [number, number];
  gpuMs: number | null;
  shaderChars: number;
  compileMs: number;
}

export interface NearFieldRenderer {
  /** (Re)compile the SDF program for a world: terrain, environment and archetype functions. */
  setWorld(world: WorldState): Promise<void>;
  /** Replace the instance set. Cheap: rewrites data textures, never recompiles. */
  setEntities(entities: WorldEntity[]): void;
  setQuality(profile: QualityProfile): void;
  setDebugView(view: DebugView): void;
  stats(): NearFieldStats;
}

/**
 * Implemented in Chunk 3/4 by the Gaussian-splat renderer. Declared now so the near field, the region
 * manager and the frame loop are written against the final shape. There is deliberately no stand-in
 * implementation: until a real one exists, `HybridFrame.far` is null and the UI says so.
 */
export interface FarFieldRenderer {
  /** Add a validated region asset to the shared scene. The manifest revision must match the region. */
  activate(manifest: NeuralAssetManifest, bytes: ArrayBuffer): Promise<void>;
  deactivate(coord: RegionCoord): void;
  stats(): { regions: number; splats: number; gpuMs: number | null };
}

export interface HybridFrame {
  renderer: THREE.WebGLRenderer;
  /** Shared scene: composite quad now, splat meshes later. */
  scene: THREE.Scene;
  camera: THREE.PerspectiveCamera;
  near: NearFieldRenderer;
  far: FarFieldRenderer | null;
}
