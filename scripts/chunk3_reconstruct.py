"""Chunk 3 pipeline driver: one region → captured SDF views → optimized Gaussians → validated, published asset.

    .venv\\Scripts\\python scripts\\chunk3_reconstruct.py live WORLD_ID RX RZ [--profile balanced]
    .venv\\Scripts\\python scripts\\chunk3_reconstruct.py offline RX RZ [--profile balanced]

live     Neon `dev` branch. Enqueues (or reuses) the region's NeuralGenerationJob for its *current* revision, starts a
         private companion on port 8775 so the capture page reads the authoritative region through the normal API,
         captures in Chrome, trains in WSL2, re-validates the PLY here, writes it to the local AssetStore and publishes
         it with the compare-and-set transaction (a job whose region changed meanwhile ends `superseded`).
offline  Bundled sample world, no database: the asset and a manifest go to apps/web/public/neural-offline/
         (git-ignored) for the browser's Gaussian diagnostic view.

Prerequisite: `npm run build` in apps/web (the capture uses the production build).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services"), str(ROOT / "packages" / "contracts" / "python")]

from gpu.ply import decode_ply, validate_gaussians  # noqa: E402

DATA = ROOT / "data"
WEB = ROOT / "apps" / "web"
OFFLINE_OUT = WEB / "public" / "neural-offline"
COMPANION_PORT = 8775
CAPTURE_PORT = 4174
BOUNDS_MARGIN_M = 2.0
WSL_DISTRO = os.environ.get("GREALITY_WSL_DISTRO", "Ubuntu")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def wsl_path(p: Path) -> str:
    drive, rest = p.resolve().drive, p.resolve().as_posix()[2:]
    return f"/mnt/{drive[0].lower()}{rest}"


def capture(rx: int, rz: int, out: Path, job_id: str, query: str, hash_: str = "") -> dict:
    if out.exists():
        shutil.rmtree(out)
    cmd = ["node", "scripts/capture-region.mjs", f"--rx={rx}", f"--rz={rz}", f"--out={out}", f"--job={job_id}",
           f"--query={query}", f"--port={CAPTURE_PORT}"]
    if hash_:
        cmd.append(f"--hash={hash_}")
    r = subprocess.run(cmd, cwd=WEB, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"capture failed: {(r.stderr or r.stdout).strip()[-800:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def train(bundle: Path, out: Path, profile: str) -> dict:
    if out.exists():
        shutil.rmtree(out)
    cmd = ["wsl.exe", "-d", WSL_DISTRO, "--cd", wsl_path(ROOT), "--", "bash", "scripts/wsl-gpu.sh", "python", "-m",
           "gpu.reconstruct", "--bundle", wsl_path(bundle), "--out", wsl_path(out), "--profile", profile]
    t = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:
        if line.startswith(("it ", "gaussians", "problem")) or "Error" in line:
            log(f"  gpu | {line.rstrip()}")
    code = proc.wait()
    result = json.loads((out / "result.json").read_text()) if (out / "result.json").exists() else {}
    metrics = json.loads((out / "metrics.json").read_text()) if (out / "metrics.json").exists() else {}
    log(f"training process exited {code} after {time.perf_counter() - t:.0f}s")
    return {"exit": code, "result": result, "metrics": metrics}


def validate_asset(train_out: Path, bundle: dict, metrics: dict, min_psnr: float) -> tuple[bytes, list[str]]:
    """Independent re-check on this side of the WSL boundary: decode the file and validate it from scratch."""
    data = (train_out / "gaussians.ply").read_bytes()
    problems = []
    if hashlib.sha256(data).hexdigest() != metrics.get("sha256"):
        problems.append("PLY sha256 differs from the trainer's report")
    g = decode_ply(data)
    origin = bundle["capture"]["region_origin"]
    lo = [bundle["capture"]["clip_min"][i] - origin[i] - BOUNDS_MARGIN_M for i in range(3)]
    hi = [bundle["capture"]["clip_max"][i] - origin[i] + BOUNDS_MARGIN_M for i in range(3)]
    problems += validate_gaussians(g, lo, hi)
    if g.count != metrics.get("gaussian_count"):
        problems.append(f"PLY has {g.count} Gaussians, trainer reported {metrics.get('gaussian_count')}")
    if not (metrics.get("psnr_holdout") or 0) >= min_psnr:
        problems.append(f"holdout PSNR {metrics.get('psnr_holdout')} below {min_psnr} dB")
    return data, problems


def manifest_fields(metrics: dict, capture_seconds: float) -> dict:
    return {
        "rasterizer": metrics["rasterizer"], "iterations": metrics["iterations"],
        "gaussian_count": metrics["gaussian_count"], "capture_seconds": capture_seconds,
        "train_seconds": metrics["train_seconds"], "psnr_holdout": metrics["psnr_holdout"],
        "peak_vram_mb": metrics["peak_reserved_mb"],
    }


# ------------------------------------------------------------------------------------------------ offline

def run_offline(rx: int, rz: int, profile: str, min_psnr: float) -> int:
    job_id = f"offline-{rx}-{rz}"
    bundle_dir = DATA / "bundles" / job_id
    train_dir = DATA / "train" / job_id
    log(f"capturing offline sample region ({rx},{rz})")
    cap = capture(rx, rz, bundle_dir, job_id, "sample=1")
    log(f"captured {cap['views']} views in {cap['seconds']:.1f}s, revision {cap['region_revision']}, "
        f"coverage {cap['mean_coverage']:.1%}")
    tr = train(bundle_dir, train_dir, profile)
    if tr["exit"] not in (0, 3):
        log(f"FAILED: {tr['result'].get('problems')}")
        return 1
    bundle = json.loads((bundle_dir / "bundle.json").read_text())
    data, problems = validate_asset(train_dir, bundle, tr["metrics"], min_psnr)
    problems += tr["result"].get("problems", [])
    if problems:
        log(f"asset rejected: {problems}")
        return 1
    m = tr["metrics"]
    OFFLINE_OUT.mkdir(parents=True, exist_ok=True)
    name = f"r_{rx}_{rz}-rev{bundle['region_revision']}-{m['sha256'][:12]}.ply"
    (OFFLINE_OUT / name).write_bytes(data)
    manifest = {
        "asset_id": f"asset-{job_id}", "world_id": bundle["world_id"], "coord": {"rx": rx, "rz": rz},
        "region_revision": bundle["region_revision"], "state": "active", "method": "3dgs-optim-v1", "format": "ply",
        "uri": name, "sha256": m["sha256"], "byte_size": len(data), "gaussian_count": m["gaussian_count"],
        "sh_degree": m["sh_degree"], "coordinate_frame": "world_rh_yup_m", "origin": m["origin"],
        "bounds": m["bounds"], "metrics": manifest_fields(m, cap["seconds"]), "job_id": job_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    index_path = OFFLINE_OUT / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {"assets": []}
    index["assets"] = [a for a in index["assets"] if a["coord"] != manifest["coord"]] + [manifest]
    index_path.write_text(json.dumps(index, indent=1))
    log(f"offline asset written: {OFFLINE_OUT / name} ({len(data) / 1e6:.2f} MB, {m['gaussian_count']} Gaussians, "
        f"holdout PSNR {m['psnr_holdout']:.2f} dB)")
    return 0


# ------------------------------------------------------------------------------------------------ live

def start_companion() -> tuple[subprocess.Popen, str]:
    token = secrets.token_urlsafe(24)
    env = {**os.environ, "GREALITY_PORT": str(COMPANION_PORT), "GREALITY_TOKEN": token,
           "GREALITY_ALLOWED_ORIGINS": f"http://localhost:{CAPTURE_PORT}",
           "PYTHONPATH": f"{ROOT / 'services'};{ROOT / 'packages' / 'contracts' / 'python'}"}
    proc = subprocess.Popen([sys.executable, "-m", "world_api"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{COMPANION_PORT}/v1/health", timeout=2) as r:
                if json.loads(r.read())["database"] == "connected":
                    return proc, token
        except Exception:
            pass
        if proc.poll() is not None:
            raise RuntimeError("capture companion exited during start-up")
        time.sleep(0.5)
    proc.terminate()
    raise RuntimeError("capture companion did not become healthy")


def run_live(world_id: str, rx: int, rz: int, profile: str, min_psnr: float) -> int:
    from greality_contracts import Aabb, JobState, NeuralMetrics, QualityProfile, RegionCoord
    from world_api.assets import LocalAssetStore, asset_key
    from world_api.config import get_settings
    from world_api.interpreter import RulesInterpreter
    from world_api.service import WorldService, make_pool

    svc = WorldService(make_pool(get_settings().database_url, 2), RulesInterpreter())
    coord = RegionCoord(rx=rx, rz=rz)
    svc.load_regions(world_id, [coord])
    job = svc.enqueue_job(world_id, coord, reason="manual", quality=QualityProfile(profile))
    log(f"job {job.job_id} for region ({rx},{rz}) at revision {job.source_region_revision} ({job.state.value})")

    def fail(msg: str) -> int:
        live = svc.set_job_state(job.job_id, JobState.failed, msg[:500])
        log(f"FAILED: {msg}" + ("" if live else " (job was no longer live)"))
        return 1

    if not svc.set_job_state(job.job_id, JobState.awaiting_views):
        log("job is no longer live (superseded); nothing to do")
        return 1
    bundle_dir = DATA / "bundles" / job.job_id
    train_dir = DATA / "train" / job.job_id
    try:
        proc, token = start_companion()
        try:
            cap = capture(rx, rz, bundle_dir, job.job_id, f"world={world_id}",
                          f"companion=http://127.0.0.1:{COMPANION_PORT}&token={token}")
        finally:
            proc.terminate()
    except Exception as exc:  # noqa: BLE001
        return fail(str(exc))
    log(f"captured {cap['views']} views in {cap['seconds']:.1f}s from revision {cap['region_revision']}, "
        f"{cap['entities']} entities, coverage {cap['mean_coverage']:.1%}")
    if cap["region_revision"] != job.source_region_revision:
        return fail(f"views show revision {cap['region_revision']} but the job is for {job.source_region_revision}")

    if not svc.set_job_state(job.job_id, JobState.training):
        log("job superseded before training; stopping")
        return 1
    tr = train(bundle_dir, train_dir, profile)
    if tr["exit"] not in (0, 3):
        return fail(f"trainer error: {tr['result'].get('problems')}")
    if not svc.set_job_state(job.job_id, JobState.validating):
        log("job superseded during training; result discarded")
        return 1
    bundle = json.loads((bundle_dir / "bundle.json").read_text())
    data, problems = validate_asset(train_dir, bundle, tr["metrics"], min_psnr)
    problems += tr["result"].get("problems", [])
    if problems:
        return fail("validation: " + "; ".join(problems))

    m = tr["metrics"]
    store = LocalAssetStore()
    key = asset_key(world_id, rx, rz, job.source_region_revision, m["sha256"])
    store.put(key, data)
    status, manifest = svc.publish_asset(
        job.job_id, uri=key, sha256=m["sha256"], byte_size=len(data), gaussian_count=m["gaussian_count"],
        fmt="ply", sh_degree=m["sh_degree"], origin=m["origin"],
        bounds=Aabb(min=m["bounds"]["min"], max=m["bounds"]["max"]),
        metrics=NeuralMetrics(**manifest_fields(m, cap["seconds"])))
    if status != "published":
        store.delete(key)
        log(f"region changed while the job ran: job {job.job_id} superseded, asset discarded")
        return 2
    log(f"published {manifest.asset_id} for ({rx},{rz}) revision {manifest.region_revision}: "
        f"{manifest.gaussian_count} Gaussians, {manifest.byte_size / 1e6:.2f} MB, holdout PSNR {m['psnr_holdout']:.2f} dB")
    log(f"stored at {store.path(key)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    live = sub.add_parser("live")
    live.add_argument("world_id")
    off = sub.add_parser("offline")
    for p in (live, off):
        p.add_argument("rx", type=int)
        p.add_argument("rz", type=int)
        p.add_argument("--profile", default="balanced", choices=["performance", "balanced", "high", "ultra"])
        p.add_argument("--min-psnr", type=float, default=20.0)
    a = ap.parse_args()
    if a.mode == "offline":
        return run_offline(a.rx, a.rz, a.profile, a.min_psnr)
    return run_live(a.world_id, a.rx, a.rz, a.profile, a.min_psnr)


if __name__ == "__main__":
    sys.exit(main())
