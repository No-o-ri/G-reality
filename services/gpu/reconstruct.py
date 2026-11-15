"""Command-line entry of the GPU worker: view bundle → optimized Gaussian asset + metrics.

    bash scripts/wsl-gpu.sh python -m gpu.reconstruct --bundle data/bundles/<job> --out data/train/<job> [--profile balanced]

Writes <out>/gaussians.ply, <out>/metrics.json, <out>/holdout_XX.png (capture | reconstruction | 4× error) and
<out>/result.json ({"ok", "problems"}). Exit status: 0 valid asset, 3 asset failed validation, 2 error.
It never touches the database: the Windows orchestrator validates again and publishes (scripts/chunk3_reconstruct.py).
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import replace
from pathlib import Path

# iterations and Gaussian budget per quality profile (docs/architecture §11, measured in docs/testing/CHUNK3_RESULTS.md)
PROFILES = {
    "performance": {"iterations": 600, "max_gaussians": 15_000},
    "balanced": {"iterations": 1500, "max_gaussians": 25_000},
    "high": {"iterations": 2500, "max_gaussians": 40_000},
    "ultra": {"iterations": 3500, "max_gaussians": 60_000},
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--profile", default="balanced", choices=sorted(PROFILES))
    ap.add_argument("--iterations", type=int)
    ap.add_argument("--max-gaussians", type=int)
    ap.add_argument("--vram-cap-gib", type=float)
    ap.add_argument("--min-psnr", type=float)
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    result_path = args.out / "result.json"
    try:
        from gpu.bundle import load_bundle
        from gpu.optimizer.train import TrainConfig, train

        cfg = replace(TrainConfig(), **PROFILES[args.profile])
        for name, value in (("iterations", args.iterations), ("max_gaussians", args.max_gaussians),
                            ("vram_cap_gib", args.vram_cap_gib), ("min_holdout_psnr", args.min_psnr)):
            if value is not None:
                cfg = replace(cfg, **{name: value})
        bundle = load_bundle(args.bundle)

        def progress(row: dict) -> None:
            if row["it"] == 1 or row["it"] % 100 == 0:
                print(f"it {row['it']:5d}  loss {row['loss']:.4f}  l1 {row['l1']:.4f}  n {row['count']}  "
                      f"{row['t']:.1f}s", flush=True)

        result = train(bundle, args.out, cfg, progress=progress)
        m = result.metrics
        if "psnr_holdout" in m:
            print(f"gaussians {m['gaussian_count']}  holdout PSNR {m['psnr_holdout']:.2f} dB  "
                  f"train {m['train_seconds']:.1f}s  peak reserved {m['peak_reserved_mb']:.0f} MiB  "
                  f"asset {m['asset_bytes'] / 1e6:.2f} MB", flush=True)
        result_path.write_text(json.dumps({"ok": result.ok, "problems": result.problems}, indent=1))
        for p in result.problems:
            print(f"problem: {p}", file=sys.stderr)
        return 0 if result.ok else 3
    except Exception as exc:  # noqa: BLE001 - reported to the orchestrator
        traceback.print_exc()
        result_path.write_text(json.dumps({"ok": False, "problems": [f"{type(exc).__name__}: {exc}"]}, indent=1))
        return 2


if __name__ == "__main__":
    sys.exit(main())
