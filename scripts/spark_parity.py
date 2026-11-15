"""Compare Spark renders of a published asset (apps/web/scripts/spark-parity.mjs) with the SDF capture and with the
training rasterizer's render of the same held-out cameras (middle panel of holdout_XX.png). Needs Pillow (WSL venv).

    bash scripts/wsl-gpu.sh python scripts/spark_parity.py data/bundles/offline-0-0 data/train/offline-0-0

PSNR is over observed pixels, everything composited on black. Spark ↔ torch measures how faithfully the browser
renders the asset the optimizer produced (file format, coordinate frame, SH convention, splat footprint, sorting).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "services")]
from gpu.bundle import load_bundle  # noqa: E402


def psnr(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float:
    mse = float(((a - b) ** 2)[mask].mean())
    return float("inf") if mse == 0 else 10 * math.log10(1 / mse)


def main(bundle_dir: str, train_dir: str) -> int:
    bundle = load_bundle(bundle_dir)
    rows = []
    for v in bundle.holdout_views():
        spark = np.asarray(Image.open(Path(train_dir) / f"spark_{v.index:02d}.png").convert("RGB"), np.float32) / 255
        panel = np.asarray(Image.open(Path(train_dir) / f"holdout_{v.index:02d}.png").convert("RGB"), np.float32) / 255
        torch_render = panel[:, v.width:2 * v.width]
        gt = v.rgb * v.alpha[..., None]
        mask = np.broadcast_to(v.observed[..., None], gt.shape)
        rows.append({"view": v.index, "spark_vs_capture": psnr(spark, gt, mask), "torch_vs_capture": psnr(torch_render, gt, mask),
                     "spark_vs_torch": psnr(spark, torch_render, mask)})
    for r in rows:
        print(f"view {r['view']:2d}  Spark↔capture {r['spark_vs_capture']:6.2f} dB  torch↔capture {r['torch_vs_capture']:6.2f} dB  "
              f"Spark↔torch {r['spark_vs_torch']:6.2f} dB")
    summary = {k: float(np.mean([r[k] for r in rows])) for k in ("spark_vs_capture", "torch_vs_capture", "spark_vs_torch")}
    print("mean", json.dumps({k: round(x, 2) for k, x in summary.items()}))
    (Path(train_dir) / "spark_parity.json").write_text(json.dumps({"views": rows, "mean": summary}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
