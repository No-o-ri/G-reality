"""Regenerate deterministic Python reference vectors from the repository root."""
from pathlib import Path
import json
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services"), str(ROOT / "packages/contracts/python")]

from greality_contracts import TerrainDefinition
from world_api.archetypes import library
from world_api.terrain import hash12, vnoise, terrain_height, terrain_slope


def write(name, data):
    path = ROOT / "tests/golden" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n")


def main():
    rng = random.Random(20261114)
    mat = {"id": "ground", "base_color": [0.3, 0.4, 0.2]}
    configs = [
        {"amplitude": 24, "frequency": 0.008, "octaves": 5},
        {"base_height": 12, "amplitude": 180, "frequency": 0.013, "octaves": 8, "ridge": 0.9, "warp": 67},
        {"base_height": 8, "amplitude": 16, "frequency": 0.006, "octaves": 6, "ridge": 0.2,
         "warp": 12, "basin_center": [-120, 87], "basin_radius": 320, "basin_depth": 45,
         "water_level": 2, "water_material": {"id": "water", "base_color": [0.1, 0.3, 0.6]}},
    ]
    points = [[0, 0], [-3000, 3000], [3000, -3000], [-120, 87], [200, 87],
              [-0.001, 0.001], [64, -64], [-1024.25, 2048.5]]
    points += [[rng.uniform(-3000, 3000), rng.uniform(-3000, 3000)] for _ in range(32)]
    cases = []
    for config in configs:
        terrain = TerrainDefinition(ground_material=mat, slope_material=mat, **config)
        for seed in [0, 987654321]:
            cases.append({"terrain": terrain.model_dump(mode="json"), "seed": seed, "points": points,
                          "heights": [terrain_height(terrain, seed, x, z) for x, z in points],
                          "slopes": [terrain_slope(terrain, seed, x, z) for x, z in points]})
    hashes = []
    for i in range(200):
        x, y = points[i] if i < len(points) else (rng.uniform(-3000, 3000), rng.uniform(-3000, 3000))
        hashes.append({"x": x, "y": y, "value": float(hash12(x, y))})
    noise = []
    for i in range(100):
        x, y = rng.uniform(-3000, 3000), rng.uniform(-3000, 3000)
        seed = [0, 1, -1, 987654321, 1048575][i % 5]
        noise.append({"x": x, "y": y, "seed": seed, "value": float(vnoise(x, y, seed))})
    write("terrain_v1.json", {"cases": cases, "hash12": hashes, "vnoise": noise})
    write("archetypes.json", [v.model_dump(mode="json") for _, v in sorted(library().items())])
    print(f"Wrote {len(cases)} terrain cases, 200 hashes, 100 noise samples and {len(library())} archetypes")


if __name__ == "__main__":
    main()
