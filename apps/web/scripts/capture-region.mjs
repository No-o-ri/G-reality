/**
 * Capture the reconstruction views of one region with the near-field SDF renderer in Chrome on the real GPU.
 *
 *   npm run build && node scripts/capture-region.mjs --rx=0 --rz=0 --out=<dir> [--job=<id>] [--size=256]
 *        [--query=sample=1 | --query=world=<id>] [--hash=companion=http://127.0.0.1:8765&token=…]
 *
 * Writes the view bundle read by services/gpu/bundle.py: bundle.json, view_XX.png (sRGB8 RGBA, alpha = coverage)
 * and view_XX.depth.f32 (linear depth, float32 LE, row 0 at the top; 0 = empty, −1 = unobserved). Prints one JSON
 * summary line at the end.
 * The pixels come from the page's own renderer; this script only transports and encodes them (PNG is written here
 * rather than through a canvas so 8-bit color is stored exactly, without premultiplied-alpha rounding).
 */
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { deflateSync } from "node:zlib";
import { chromium } from "playwright-core";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const arg = (name, fallback) => process.argv.find((a) => a.startsWith(`--${name}=`))?.slice(name.length + 3) ?? fallback;
const rx = Number(arg("rx", "0"));
const rz = Number(arg("rz", "0"));
const size = Number(arg("size", "256"));
const out = resolve(arg("out", join(root, "visual-out", `capture_${rx}_${rz}`)));
const jobId = arg("job", `offline-${rx}-${rz}`);
const query = arg("query", "sample=1");
const hash = arg("hash", "");
const PORT = Number(arg("port", "4174"));
const BASE = `http://localhost:${PORT}`;
if (!Number.isInteger(rx) || !Number.isInteger(rz)) throw new Error("--rx and --rz must be integers");

// ---------------------------------------------------------------- PNG (RGBA8, no filtering)
const CRC = new Uint32Array(256).map((_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(buf) {
  let c = 0xffffffff;
  for (const b of buf) c = CRC[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const head = Buffer.alloc(8);
  head.writeUInt32BE(data.length, 0);
  head.write(type, 4, "ascii");
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])), 0);
  return Buffer.concat([head, data, crc]);
}
function encodePng(width, height, rgba) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr.set([8, 6, 0, 0, 0], 8); // 8-bit, RGBA, deflate, no filter method, no interlace
  const raw = Buffer.alloc((width * 4 + 1) * height);
  for (let y = 0; y < height; y++) {
    raw[y * (width * 4 + 1)] = 0;
    rgba.copy(raw, y * (width * 4 + 1) + 1, y * width * 4, (y + 1) * width * 4);
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw, { level: 6 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

// ---------------------------------------------------------------- browser
const browsers = ["C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"];
const exe = browsers.find((p) => existsSync(p));
if (!exe) throw new Error("No Chrome or Edge installation found.");

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

const t0 = Date.now();
const browser = await chromium.launch({ executablePath: exe, headless: true, args: ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist"] });
let summary;
try {
  const page = await (await browser.newContext({ viewport: { width: 640, height: 480 } })).newPage();
  const errors = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text().slice(0, 300)));
  page.on("pageerror", (e) => errors.push(String(e).slice(0, 300)));
  await page.goto(`${BASE}/?${query}&q=high${hash ? `#${hash}` : ""}`);
  await page.waitForFunction(() => window.__greality && (window.__greality.ready || window.__greality.error), null, { timeout: 90_000 });
  const pageError = await page.evaluate(() => window.__greality.error);
  if (pageError) throw new Error(`page error: ${pageError}`);
  const gpu = await page.evaluate(() => window.__greality.status().gpuRenderer);
  if (/swiftshader|basic render|llvmpipe/i.test(gpu)) throw new Error(`software renderer: ${gpu}`);

  const session = await page.evaluate(({ rx, rz, size }) => window.__greality.engine.beginCapture({ rx, rz }, size), { rx, rz, size });
  mkdirSync(out, { recursive: true });
  const perView = [];
  for (const v of session.views) {
    const r = await page.evaluate((i) => window.__greality.engine.captureView(i), v.index);
    const rgba = Buffer.from(r.rgba, "base64");
    const depth = Buffer.from(r.depth, "base64");
    if (rgba.length !== size * size * 4 || depth.length !== size * size * 4) throw new Error(`view ${v.index}: wrong pixel count`);
    const name = `view_${String(v.index).padStart(2, "0")}`;
    writeFileSync(join(out, `${name}.png`), encodePng(size, size, rgba));
    writeFileSync(join(out, `${name}.depth.f32`), depth);
    perView.push({ index: v.index, holdout: v.holdout, hitFraction: r.hitFraction, unobservedFraction: r.unobservedFraction, ms: r.ms });
  }
  await page.evaluate(() => window.__greality.engine.endCapture());
  const seconds = (Date.now() - t0) / 1000;
  const empty = perView.filter((v) => v.hitFraction === 0);
  if (empty.length) throw new Error(`views with no surface inside the clip box: ${empty.map((v) => v.index).join(", ")}`);
  if (errors.length) throw new Error(`console error: ${errors[0]}`);
  const bundle = { job_id: jobId, ...session, capture: { ...session.capture, seconds, per_view: perView } };
  writeFileSync(join(out, "bundle.json"), JSON.stringify(bundle, null, 1));
  summary = {
    ok: true, out, world_id: session.world_id, coord: session.coord, region_revision: session.region_revision,
    views: perView.length, seconds, gpu, entities: session.capture.entities,
    mean_coverage: perView.reduce((a, v) => a + v.hitFraction, 0) / perView.length,
    render_ms_mean: perView.reduce((a, v) => a + v.ms, 0) / perView.length,
  };
} finally {
  await browser.close();
  server.kill();
}
console.log(JSON.stringify(summary));
process.exit(0);
