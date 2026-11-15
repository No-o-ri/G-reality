/**
 * End-to-end playtest through the real player UI, on the real GPU, against the real companion + Neon.
 *
 *   node scripts/play-test.mjs --base=http://127.0.0.1:8781 --token=<pairing token> --prompt="..." [--offline]
 *        [--out=<dir>] [--fire] [--name=<prefix>]
 *
 * The companion must be serving the built frontend (GREALITY_WEB_DIR) at --base, as the release launcher does.
 * Steps: landing page → type the prompt → click "Generate world" → wait until in the world → eye-level screenshots
 * in four directions → (with --fire) walk to the nearest tree, check the "E — Burn it?" prompt, press E, watch the
 * fire for ~75 s, leave the area and come back, then close the page, reopen the world from the landing page's
 * "Your worlds" list and check that the server-side state persisted. Writes screenshots and report.json.
 *
 * Automation shortcuts (stated, not hidden): headless Chrome cannot hold pointer lock, so the script marks the player
 * as locked and sets yaw/pitch/position directly; movement to the tree is a teleport to 3.5 m in front of it.
 */
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { chromium } from "playwright-core";

const arg = (n, d) => process.argv.find((a) => a.startsWith(`--${n}=`))?.slice(n.length + 3) ?? d;
const flag = (n) => process.argv.includes(`--${n}`);
const base = arg("base", "http://127.0.0.1:8781");
const token = arg("token");
const promptText = arg("prompt", "A dense alpine forest of tall pines around a cold blue lake, snowy mountains behind, a small wooden settlement on the shore");
const out = resolve(arg("out", "visual-out/play"));
const name = arg("name", "world");
const offline = flag("offline");
const fire = flag("fire");
mkdirSync(out, { recursive: true });
const exe = ["C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find((p) => existsSync(p));
const url = `${base}/#companion=${base}&token=${token}`;
const report = { prompt: promptText, offline, steps: [], failures: [] };
const step = (s, extra = {}) => {
  const row = { ...extra, at: new Date().toISOString(), step: s };
  report.steps.push(row);
  console.log(`[${row.at.slice(11, 19)}] ${s}${Object.keys(extra).length ? " " + JSON.stringify(extra) : ""}`);
};
const fail = (why) => {
  report.failures.push(why);
  console.log(`FAIL: ${why}`);
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await chromium.launch({ executablePath: exe, headless: true, args: ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist", "--window-size=1600,900", "--enable-precise-memory-info"] });
const context = await browser.newContext({ viewport: { width: 1600, height: 900 } });
let page = await context.newPage();
const consoleErrors = [];
const watch = (p) => {
  p.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text().slice(0, 300)));
  p.on("pageerror", (e) => consoleErrors.push(String(e).slice(0, 300)));
};
watch(page);
const shot = async (file) => page.screenshot({ path: join(out, `${name}-${file}.png`) });

async function status() {
  return page.evaluate(() => {
    const g = window.__greality;
    const s = g.status();
    const mem = performance.memory ? { usedMB: performance.memory.usedJSHeapSize / 1048576 } : null;
    return { world: s.worldId, rev: s.worldRevision, fps: s.fps, frameMs: s.frameMs, nearGpuMs: s.nearGpuMs, pos: s.position,
      instances: s.instances, archetypes: s.archetypes, regions: s.regionsLoaded, loadError: s.loadError, mem, compileMs: s.compileMs, shaderChars: s.shaderChars,
      locomotion: g.engine.player.locomotion };
  });
}
async function lockAndLook(yaw, pitch) {
  await page.evaluate(([yaw, pitch]) => {
    const p = window.__greality.engine.player;
    p.locked = true;
    p.yaw = yaw;
    p.pitch = pitch;
  }, [yaw, pitch]);
}

try {
  await page.goto(url);
  await page.waitForFunction(() => window.__greality?.ready, null, { timeout: 90_000 });
  await page.waitForSelector(".landing .status-line .ok", { timeout: 30_000 });
  await sleep(2500);
  await shot("00-landing");
  step("landing page shown");

  await page.fill(".prompt-box textarea", promptText);
  const t0 = Date.now();
  await page.click(".prompt-box button.primary");
  step("Generate world clicked");
  await page.waitForSelector(".progress", { timeout: 10_000 });
  await shot("01-generating");
  await page.waitForFunction(() => document.querySelector(".world-title") || document.querySelector(".error-box"), null, { timeout: 180_000 });
  const err = await page.$(".error-box");
  if (err) {
    const msg = await err.innerText();
    fail(`generation error: ${msg}`);
    if (offline || /Gemini/.test(msg)) {
      step("using the offline interpreter button offered by the UI");
      await page.click(".error-box button.secondary");
      await page.waitForSelector(".world-title", { timeout: 180_000 });
    } else throw new Error(msg);
  }
  const genSeconds = (Date.now() - t0) / 1000;
  const world = await page.evaluate(() => {
    const w = window.__greality.engine.world;
    return { id: w.world_id, name: w.name, notes: w.spec.interpretation_notes, terrain: w.spec.terrain, archetypes: w.spec.archetypes.map((a) => a.id), features: w.spec.features.length, env: w.environment };
  });
  step("entered world", { seconds: genSeconds, id: world.id, name: world.name, notes: world.notes, archetypes: world.archetypes.length });
  report.world = world;
  await sleep(4000);
  for (const [i, yaw] of [0, Math.PI / 2, Math.PI, -Math.PI / 2].entries()) {
    await lockAndLook(yaw, -0.05);
    await sleep(1500);
    await shot(`02-eye-${i}`);
  }
  const s1 = await status();
  step("eye-level views", s1);
  report.perf = [s1];

  if (fire) {
    const target = await page.evaluate(() => {
      const eng = window.__greality.engine;
      const p = eng.player.position;
      const trees = eng.loadedEntities().filter((e) => e.kind === "tree" && e.state.condition === "intact" && e.capabilities.includes("flammable"));
      trees.sort((a, b) => Math.hypot(a.transform.position[0] - p.x, a.transform.position[2] - p.z) - Math.hypot(b.transform.position[0] - p.x, b.transform.position[2] - p.z));
      // prefer a tree with several neighbours so spreading can be observed
      const score = (t) => trees.filter((o) => o !== t && Math.hypot(o.transform.position[0] - t.transform.position[0], o.transform.position[2] - t.transform.position[2]) < 9).length;
      const pick = trees.slice(0, 40).sort((a, b) => score(b) - score(a))[0];
      if (!pick) return null;
      const [x, , z] = pick.transform.position;
      // stand 3.5 m away on the side facing the most open ground
      let best = null;
      for (let k = 0; k < 16; k++) {
        const a = (k / 16) * Math.PI * 2;
        const sx = x + Math.cos(a) * 3.5, sz = z + Math.sin(a) * 3.5;
        const clear = eng.near.collide(sx, eng.near.groundHeight(sx, sz), sz, 0.35, 1.85);
        const moved = Math.hypot(clear[0] - sx, clear[1] - sz);
        if (!best || moved < best.moved) best = { sx, sz, moved };
      }
      eng.player.position.set(best.sx, eng.near.groundHeight(best.sx, best.sz) + 1.7, best.sz);
      const dx = x - best.sx, dz = z - best.sz;
      eng.player.yaw = Math.atan2(-dx, -dz);
      eng.player.pitch = Math.atan2(1.2, Math.hypot(dx, dz)) * 0.6;
      return { id: pick.id, neighbours: score(pick) };
    });
    if (!target) fail("no intact tree loaded");
    else {
      step("walked up to a tree", target);
      await page.evaluate(() => { window.__greality.engine.player.locked = true; });
      await sleep(1200);
      const promptTextShown = await page.$eval(".interact", (el) => el.innerText).catch(() => null);
      step("interaction prompt", { text: promptTextShown });
      if (!promptTextShown || !/Burn it\?/.test(promptTextShown)) fail(`expected "E — Burn it?" prompt, got ${JSON.stringify(promptTextShown)}`);
      await shot("03-prompt");
      await page.keyboard.press("KeyE");
      step("pressed E");
      await page.waitForSelector(".toast", { timeout: 15_000 }).catch(() => fail("no toast after E"));
      const toastText = await page.$eval(".toast", (el) => el.innerText).catch(() => null);
      step("server response", { toast: toastText });
      // step back to watch the fire
      await page.evaluate((id) => {
        const eng = window.__greality.engine;
        const e = eng.loadedEntities().find((x) => x.id === id);
        const [x, , z] = e.transform.position;
        const p = eng.player.position;
        const dx = p.x - x, dz = p.z - z, d = Math.hypot(dx, dz) || 1;
        const nx = x + (dx / d) * 14, nz = z + (dz / d) * 14;
        p.set(nx, eng.near.groundHeight(nx, nz) + 1.7, nz);
        eng.player.yaw = Math.atan2(-(x - nx), -(z - nz));
        eng.player.pitch = 0.12;
      }, target.id);
      const fireRows = [];
      for (const t of [3, 10, 20, 35, 50, 70, 95]) {
        const wait = t * 1000 - (fireRows.length ? fireRows[fireRows.length - 1].t * 1000 : 0);
        await sleep(wait);
        await page.evaluate(() => { window.__greality.engine.player.locked = true; });
        const counts = await page.evaluate(() => {
          const ents = window.__greality.engine.loadedEntities();
          const c = (k) => ents.filter((e) => e.state.condition === k).length;
          return { burning: c("burning"), charred: c("charred"), particles: window.__greality.engine.fire.particleCount() };
        });
        const st = await status();
        fireRows.push({ t, ...counts, fps: st.fps, p95: st.frameMs.p95, rev: st.rev });
        await shot(`04-fire-${String(t).padStart(3, "0")}s`);
        step(`fire +${t}s`, fireRows[fireRows.length - 1]);
      }
      report.fire = fireRows;
      if (!fireRows.some((r) => r.burning > 1 || r.charred > 1)) fail("fire did not spread to another tree within 95 s");
      if (!fireRows.some((r) => r.charred >= 1)) fail("no tree reached the charred state within 95 s");

      // leave and come back
      await page.evaluate(() => {
        const eng = window.__greality.engine;
        const p = eng.player.position;
        p.set(p.x + 260, eng.near.groundHeight(p.x + 260, p.z) + 1.7, p.z);
      });
      await sleep(6000);
      step("left the area", await status());
      await page.evaluate((id) => {
        const eng = window.__greality.engine;
        window.__targetBack = id;
      }, target.id);
      await page.evaluate(() => {
        const eng = window.__greality.engine;
        const p = eng.player.position;
        p.set(p.x - 260, eng.near.groundHeight(p.x - 260, p.z) + 1.7, p.z);
      });
      await sleep(6000);
      await shot("05-returned");
      const back = await page.evaluate((id) => window.__greality.engine.loadedEntities().find((e) => e.id === id)?.state.condition, target.id);
      step("returned", { targetCondition: back });
      if (back === "intact") fail("the burned tree is intact after returning");

      // close and reopen
      const beforeClose = await page.evaluate(() => {
        const ents = window.__greality.engine.loadedEntities();
        return Object.fromEntries(ents.filter((e) => e.state.condition !== "intact").map((e) => [e.id, e.state.condition]));
      });
      await page.close();
      step("page closed", { changedEntities: Object.keys(beforeClose).length });
      await sleep(3000);
      page = await context.newPage();
      watch(page);
      await page.goto(url);
      await page.waitForFunction(() => window.__greality?.ready, null, { timeout: 90_000 });
      await page.waitForSelector(".world-card", { timeout: 30_000 });
      const cards = await page.$$eval(".world-card .name", (els) => els.map((e) => e.textContent));
      const idx = cards.indexOf(world.name);
      await (await page.$$(".world-card"))[Math.max(0, idx)].click();
      await page.waitForSelector(".world-title", { timeout: 90_000 });
      await sleep(5000);
      const afterOpen = await page.evaluate((ids) => {
        const ents = new Map(window.__greality.engine.loadedEntities().map((e) => [e.id, e.state.condition]));
        return Object.fromEntries(ids.map((id) => [id, ents.get(id) ?? "not loaded"]));
      }, Object.keys(beforeClose));
      const regressions = Object.entries(afterOpen).filter(([, c]) => c === "intact");
      step("reopened from the landing page", { checked: Object.keys(afterOpen).length, intactAgain: regressions.length });
      if (regressions.length) fail(`entities intact again after reopening: ${regressions.map(([id]) => id).join(", ")}`);
      await page.evaluate((id) => {
        const eng = window.__greality.engine;
        const e = eng.loadedEntities().find((x) => x.id === id);
        if (!e) return;
        const [x, , z] = e.transform.position;
        eng.player.position.set(x + 9, eng.near.groundHeight(x + 9, z) + 1.7, z);
        eng.player.yaw = Math.atan2(9, 0);
        eng.player.pitch = 0.1;
        eng.player.locked = true;
      }, target.id);
      await sleep(3000);
      await shot("06-reopened-aftermath");
    }
  }
  report.perf.push(await status());
} catch (e) {
  fail(`exception: ${e.message}`);
} finally {
  report.consoleErrors = consoleErrors;
  writeFileSync(join(out, `${name}-report.json`), JSON.stringify(report, null, 2));
  await browser.close();
}
if (consoleErrors.length) console.log(`console errors: ${consoleErrors.length}: ${consoleErrors[0]}`);
console.log(report.failures.length ? `\n${report.failures.length} FAILURE(S)` : "\nPLAYTEST PASS");
process.exit(report.failures.length ? 1 : 0);
