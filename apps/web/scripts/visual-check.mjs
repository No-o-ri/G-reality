/**
 * Automated visual check of the near-field renderer in a real browser on the real GPU.
 *
 *   npm run build && npm run visual            (from apps/web)
 *
 * Serves the production build, opens it in the installed Chrome (or Edge) with the offline sample world,
 * and for several camera poses / debug views records: shader compile errors, console errors, the
 * GPU↔CPU terrain parity, frame statistics and a screenshot. Exits non-zero if a check fails.
 * --gaussians=RX,RZ adds the Chunk 3 diagnostic shots (SDF / Gaussian-only / split) framing that region; they require
 * an optimized asset for it (scripts/chunk3_reconstruct.py) and fail unless it loads, verifies and renders.
 * Screenshots and report.json are written to apps/web/visual-out/ (git-ignored).
 */
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = join(root, "visual-out");
mkdirSync(out, { recursive: true });
const PORT = 4173;
const BASE = `http://localhost:${PORT}`;
const headed = process.argv.includes("--headed");
const extraQuery = process.argv.find((a) => a.startsWith("--query="))?.slice(8) ?? "sample=1";
// --hash=companion=http://127.0.0.1:8765&token=… pairs the page with a running companion (live world test)
const hash = process.argv.find((a) => a.startsWith("--hash="))?.slice(7) ?? "";
const only = process.argv.find((a) => a.startsWith("--only="))?.slice(7);
const outPrefix = process.argv.find((a) => a.startsWith("--prefix="))?.slice(9) ?? "";

const browsers = [
  ["chrome", "C:/Program Files/Google/Chrome/Application/chrome.exe"],
  ["msedge", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"],
];
const found = browsers.find(([, path]) => existsSync(path));
if (!found) {
  console.error("No Chrome or Edge installation found.");
  process.exit(2);
}

// name, pose "x,y,z,yaw,pitch" (null = spawn pose on the ground, "region:rx,rz" = frame that region), debug view,
// quality, Gaussian diagnostic mode
const SHOTS = [
  ["01-spawn-final", null, "final", "high"],
  ["02-overview-final", "-150,55,60,-0.9,-0.38", "final", "high"],
  ["03-overview-normals", "-150,55,60,-0.9,-0.38", "normals", "high"],
  ["04-overview-steps", "-150,55,60,-0.9,-0.38", "steps", "high"],
  ["05-overview-materials", "-150,55,60,-0.9,-0.38", "materials", "high"],
  ["06-overview-depth", "-150,55,60,-0.9,-0.38", "depth", "high"],
  ["07-village-final", "150,14,-38,2.4,-0.12", "final", "high"],
  ["08-lake-final", "-95,9,10,-1.45,-0.05", "final", "ultra"],
  ["09-spawn-performance", null, "final", "performance"],
];
const gaussianRegion = process.argv.find((a) => a.startsWith("--gaussians="))?.slice(12);
if (gaussianRegion) {
  for (const [n, mode] of [["10-region-sdf", "sdf"], ["11-region-gaussian", "gaussian"], ["12-region-split", "split"]]) {
    SHOTS.push([n, `region:${gaussianRegion}`, "final", "high", mode]);
  }
}

const server = spawn(process.execPath, [join(root, "node_modules/vite/bin/vite.js"), "preview", "--port", String(PORT), "--strictPort"], {
  cwd: root,
  stdio: "ignore",
});
const stop = () => server.kill();
process.on("exit", stop);

async function waitForServer() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(BASE);
      if (r.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("preview server did not start; run `npm run build` first");
}

const report = { browser: found[0], shots: [], failures: [] };
await waitForServer();
const browser = await chromium.launch({
  executablePath: found[1],
  headless: !headed,
  args: ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist", "--enable-unsafe-swiftshader=0", "--window-size=1600,900"],
});
try {
  const context = await browser.newContext({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
  for (const [shotName, pose, view, quality, gaussians = "sdf"] of SHOTS) {
    if (only && !shotName.startsWith(only)) continue;
    const name = outPrefix + shotName;
    const page = await context.newPage();
    const consoleErrors = [];
    page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text().slice(0, 500)));
    page.on("pageerror", (e) => consoleErrors.push(String(e).slice(0, 500)));
    const where = !pose ? "" : pose.startsWith("region:") ? `&viewRegion=${pose.slice(7)}` : `&pose=${pose}`;
    const query = `${extraQuery}&q=${quality}&debug=${view}&gaussians=${gaussians}${where}`;
    await page.goto(`${BASE}/?${query}${hash ? `#${hash}` : ""}`);
    await page.waitForFunction(() => window.__greality && (window.__greality.ready || window.__greality.error), null, { timeout: 60_000 });
    if (pose?.startsWith("region:")) {
      await page.waitForFunction(() => window.__greality.status().gaussianAssets.some((a) => a.state !== "loading"), null, { timeout: 30_000 }).catch(() => {});
    }
    // let the frame-time window fill with steady-state frames
    await page.waitForFunction(() => window.__greality.engine && window.__greality.engine.frameCount() > 150, null, { timeout: 60_000 }).catch(() => {});
    await page.waitForTimeout(1500);
    const status = await page.evaluate(() => {
      const g = window.__greality;
      if (!g.engine) return { error: g.error };
      const s = g.status();
      return {
        error: g.error, fps: s.fps, frameMs: s.frameMs, nearGpuMs: s.nearGpuMs, internalSize: s.internalSize,
        instances: s.instances, archetypes: s.archetypes, materials: s.materials, grid: s.grid,
        gridOverflow: s.gridOverflow, compileErrors: s.compileErrors, compileMs: s.compileMs,
        shaderChars: s.shaderChars, terrainParityM: s.terrainParityM, gpuRenderer: s.gpuRenderer,
        position: s.position, regionsLoaded: s.regionsLoaded, frames: g.engine.frameCount(),
        worldId: s.worldId, worldRevision: s.worldRevision, loadError: s.loadError,
        gaussianMode: s.gaussianMode, gaussianSplats: s.gaussianSplats, gaussianAssets: s.gaussianAssets,
      };
    });
    // image statistics from the canvas: reject blank / single-colour frames
    const stats = await page.evaluate(async () => {
      const canvas = document.querySelector("canvas.viewport");
      await new Promise((r) => requestAnimationFrame(r));
      const bitmap = await createImageBitmap(canvas);
      const c = new OffscreenCanvas(160, 90);
      const ctx = c.getContext("2d");
      ctx.drawImage(bitmap, 0, 0, 160, 90);
      const d = ctx.getImageData(0, 0, 160, 90).data;
      let sum = 0, sum2 = 0;
      const colors = new Set();
      for (let i = 0; i < d.length; i += 4) {
        const l = 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
        sum += l; sum2 += l * l;
        colors.add((d[i] >> 3) << 10 | (d[i + 1] >> 3) << 5 | (d[i + 2] >> 3));
      }
      const n = d.length / 4;
      return { meanLuma: sum / n, stdLuma: Math.sqrt(Math.max(0, sum2 / n - (sum / n) ** 2)), distinctColors: colors.size };
    });
    await page.keyboard.press("Tab"); // hide side panels, keep diagnostics
    await page.waitForTimeout(200);
    await page.screenshot({ path: join(out, `${name}.png`) });
    const shot = { name, view, quality, pose, ...status, image: stats, consoleErrors };
    report.shots.push(shot);
    const fail = (why) => report.failures.push(`${name}: ${why}`);
    if (status.error) fail(`page error: ${status.error}`);
    if (status.compileErrors?.length) fail(`shader compile error: ${status.compileErrors[0].slice(0, 200)}`);
    if (consoleErrors.length) fail(`console error: ${consoleErrors[0]}`);
    if (!(status.instances > 0)) fail("no SDF instances");
    if (pose?.startsWith("region:")) {
      const active = (status.gaussianAssets ?? []).filter((a) => a.state === "active");
      if (!active.length) fail(`no active Gaussian asset: ${JSON.stringify(status.gaussianAssets)}`);
      if (gaussians !== "sdf" && !(status.gaussianSplats > 0)) fail("Gaussian mode without any loaded Gaussians");
    }
    if (!(status.terrainParityM < 0.05)) fail(`terrain GPU/CPU mismatch ${status.terrainParityM} m`);
    if (status.gridOverflow > 0) fail(`grid overflow ${status.gridOverflow}`);
    if (!(stats.stdLuma > 6 && (view !== "final" || stats.distinctColors > 40))) fail(`image looks blank (std ${stats.stdLuma.toFixed(1)}, ${stats.distinctColors} colours)`);
    if (/swiftshader|basic render|llvmpipe/i.test(status.gpuRenderer ?? "")) fail(`software renderer: ${status.gpuRenderer}`);
    console.log(
      `${name.padEnd(24)} ${String(status.fps?.toFixed(0)).padStart(4)} fps  frame p50 ${status.frameMs?.p50.toFixed(1)} p95 ${status.frameMs?.p95.toFixed(1)} ms  ` +
        `sdf ${status.nearGpuMs == null ? "n/a" : status.nearGpuMs.toFixed(2)} ms  ${status.internalSize?.join("x")}  inst ${status.instances}  ` +
        `std ${stats.stdLuma.toFixed(1)}`,
    );
    await page.close();
  }
} finally {
  await browser.close();
  stop();
}
writeFileSync(join(out, "report.json"), JSON.stringify(report, null, 2));
const first = report.shots[0] ?? {};
console.log(`\nGPU: ${first.gpuRenderer}`);
console.log(`terrain GPU↔CPU max error: ${first.terrainParityM == null ? "n/a" : (first.terrainParityM * 1000).toFixed(3) + " mm"}`);
console.log(`shader: ${Math.round((first.shaderChars ?? 0) / 1024)} KiB, compiled in ${first.compileMs?.toFixed(0)} ms`);
if (report.failures.length) {
  console.log(`\nFAILURES (${report.failures.length}):\n  ` + report.failures.join("\n  "));
  process.exit(1);
}
console.log(`\nAll ${report.shots.length} visual checks passed. Screenshots: ${out}`);
