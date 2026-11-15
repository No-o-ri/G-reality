/**
 * Spark parity: render a published offline Gaussian asset with Spark (the browser renderer) from the exact held-out
 * cameras of its capture, so the result can be compared with the SDF capture and with the training rasterizer.
 *
 *   npm run build && node scripts/spark-parity.mjs --bundle=../../data/bundles/offline-0-0 --out=../../data/train/offline-0-0
 *   then (WSL) bash scripts/wsl-gpu.sh python scripts/spark_parity.py data/bundles/offline-0-0 data/train/offline-0-0
 *
 * Writes spark_XX.png (RGBA8 on black) next to the trainer's holdout_XX.png comparisons.
 */
import { spawn } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";
import { chromium } from "playwright-core";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const arg = (name) => process.argv.find((a) => a.startsWith(`--${name}=`))?.slice(name.length + 3);
const bundleDir = resolve(arg("bundle"));
const out = resolve(arg("out"));
const bundle = JSON.parse(readFileSync(join(bundleDir, "bundle.json"), "utf8"));
const PORT = 4175;
const BASE = `http://localhost:${PORT}`;

const CRC = new Uint32Array(256).map((_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
const crc32 = (buf) => {
  let c = 0xffffffff;
  for (const b of buf) c = CRC[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
};
const chunk = (type, data) => {
  const head = Buffer.alloc(8);
  head.writeUInt32BE(data.length, 0);
  head.write(type, 4, "ascii");
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])), 0);
  return Buffer.concat([head, data, crc]);
};
function encodePng(w, h, rgba) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0);
  ihdr.writeUInt32BE(h, 4);
  ihdr.set([8, 6, 0, 0, 0], 8);
  const raw = Buffer.alloc((w * 4 + 1) * h);
  for (let y = 0; y < h; y++) rgba.copy(raw, y * (w * 4 + 1) + 1, y * w * 4, (y + 1) * w * 4);
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk("IHDR", ihdr), chunk("IDAT", deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}

const exe = ["C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find((p) => existsSync(p));
const server = spawn(process.execPath, [join(root, "node_modules/vite/bin/vite.js"), "preview", "--port", String(PORT), "--strictPort"], { cwd: root, stdio: "ignore" });
process.on("exit", () => server.kill());
for (let i = 0; ; i++) {
  try {
    if ((await fetch(BASE)).ok) break;
  } catch {
    /* not up yet */
  }
  if (i > 60) throw new Error("preview server did not start; run `npm run build` first");
  await new Promise((r) => setTimeout(r, 250));
}
const browser = await chromium.launch({ executablePath: exe, headless: true, args: ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist"] });
const results = [];
try {
  const page = await (await browser.newContext({ viewport: { width: 640, height: 480 } })).newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(`${BASE}/?sample=1&gaussians=gaussian`);
  await page.waitForFunction(() => window.__greality?.ready || window.__greality?.error, null, { timeout: 60_000 });
  await page.waitForFunction(() => window.__greality.status().gaussianAssets.some((a) => a.state === "active"), null, { timeout: 30_000 });
  const fov = bundle.capture.rig.fovDeg;
  for (const v of bundle.views.filter((x) => x.holdout)) {
    const b64 = await page.evaluate(({ c2w, fov, size }) => window.__greality.engine.renderAssetView(c2w, fov, size), { c2w: v.camera_to_world, fov, size: v.width });
    const name = `spark_${String(v.index).padStart(2, "0")}.png`;
    writeFileSync(join(out, name), encodePng(v.width, v.height, Buffer.from(b64, "base64")));
    results.push(name);
  }
  if (errors.length) throw new Error(errors[0]);
} finally {
  await browser.close();
  server.kill();
}
console.log(JSON.stringify({ ok: true, out, renders: results }));
process.exit(0);
