/**
 * Measure how long opening a world takes in Chrome and where the time goes (developer tool).
 *   node scripts/open-timing.mjs --base=http://127.0.0.1:8781 --token=<pairing token> --world=<world id>
 * Prints the page's own setup breakdown (GLSL generation, CPU bound estimation, GPU shader compile) plus the total.
 */
import { existsSync } from "node:fs";
import { chromium } from "playwright-core";

const arg = (n, d) => process.argv.find((a) => a.startsWith(`--${n}=`))?.slice(n.length + 3) ?? d;
const base = arg("base", "http://127.0.0.1:8781");
const exe = ["C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find((p) => existsSync(p));
const browser = await chromium.launch({ executablePath: exe, headless: true, args: ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist"] });
const page = await (await browser.newContext({ viewport: { width: 1600, height: 900 } })).newPage();
page.on("console", (m) => /G-reality|error/i.test(m.text()) && console.log(`  page: ${m.text().slice(0, 300)}`));
// main-thread responsiveness: a 50 ms heartbeat; the longest gap is how long the tab was frozen
await page.addInitScript(() => {
  window.__stall = 0;
  let last = performance.now();
  setInterval(() => {
    const now = performance.now();
    window.__stall = Math.max(window.__stall, now - last - 50);
    last = now;
  }, 50);
});
const t0 = Date.now();
await page.goto(`${base}/?world=${arg("world")}#companion=${base}&token=${arg("token")}`);
await page.waitForFunction(() => window.__greality?.ready || window.__greality?.error, null, { timeout: 900_000 });
const s = await page.evaluate(() => {
  const g = window.__greality;
  const st = g.status();
  return { error: g.error, compileMs: st.compileMs, shaderKB: Math.round(st.shaderChars / 1024), archetypes: st.archetypes,
    instances: st.instances, setup: g.engine.near.setupTimings, longestMainThreadStallMs: Math.round(window.__stall) };
});
console.log(JSON.stringify({ totalSeconds: (Date.now() - t0) / 1000, ...s }));
await browser.close();
