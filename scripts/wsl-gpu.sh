#!/usr/bin/env bash
# Run a command inside the WSL2 GPU environment from the repository root.
#   Windows:  wsl.exe -d Ubuntu --cd <repo as /mnt/c/...> -- bash scripts/wsl-gpu.sh python -m pytest tests/gpu
# GREALITY_GPU_VENV overrides the venv (default /home/sidno/greality-gpu/.venv, torch 2.14.1+cu130).
set -euo pipefail
cd "$(dirname "$0")/.."
source "${GREALITY_GPU_VENV:-/home/sidno/greality-gpu/.venv}/bin/activate"
export PYTHONPATH="services:packages/contracts/python${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
# torch 2.14 routes some eager CUDA ops (e.g. bmm backward) to Triton JIT kernels, which need a C compiler that this
# distribution does not have; keep the stock ATen CUDA kernels.
export TORCH_DISABLE_NATIVE_JIT=1
exec "$@"
