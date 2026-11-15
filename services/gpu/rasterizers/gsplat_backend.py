"""gsplat compatibility probe (tier 2 of docs/architecture §5.3).

Measured 2026-11-14 in WSL2 Ubuntu (Python 3.14.4, torch 2.14.1+cu130, RTX 5070 Laptop / sm_120):
- Prebuilt gsplat wheels (docs.gsplat.studio/whl, gsplat 1.5.3) exist only up to torch 2.4 / CUDA 12.4 / CPython 3.10.
- The PyPI wheel `gsplat-1.5.3-py3-none-any.whl` ships CUDA sources (gsplat/cuda/csrc/*.cu) and JIT-compiles them on
  first use, which needs `nvcc` and a host C++ compiler. The distribution has no nvcc and no gcc/g++, and sudo needs a
  password, so neither can be installed by the agent. Triton (bundled with torch) also needs a C compiler for its
  launcher, so a Triton rasterizer is blocked for the same reason.

Unblock steps for the developer (not performed): `sudo apt install build-essential`, then inside the venv
`pip install nvidia-cuda-nvcc==13.0.88 nvidia-cuda-cccl ninja gsplat==1.5.3`, then implement and parity-test an adapter
against `torch_tiles` (the reference renderer in `reference.py` is the oracle). Until then the tile rasterizer is used.
"""

from __future__ import annotations

import shutil


def probe() -> tuple[bool, str]:
    try:
        import gsplat  # noqa: F401
    except Exception as exc:  # not installed, or import-time build failure
        missing = [tool for tool in ("nvcc", "g++") if shutil.which(tool) is None]
        hint = f"; build tools missing: {', '.join(missing)}" if missing else ""
        return False, f"import gsplat failed ({type(exc).__name__}: {exc}){hint}"
    return True, "gsplat importable"
