"""CUDA smoke test for the local GPU. Run: .venv\\Scripts\\python scripts\\cuda_smoke.py

Checks detection, tensor ops, matrix multiplication against a CPU reference, autograd on the GPU,
and that allocated memory is released. Exits non-zero on any failure.
"""

from __future__ import annotations

import sys
import time

import torch


def mb(x: int) -> float:
    return x / 2**20


def main() -> int:
    print(f"python {sys.version.split()[0]} | torch {torch.__version__} | built for CUDA {torch.version.cuda}")
    if not torch.cuda.is_available():
        print("FAIL: torch.cuda.is_available() is False")
        return 1
    dev = torch.device("cuda:0")
    props = torch.cuda.get_device_properties(0)
    free, total = torch.cuda.mem_get_info(0)
    print(f"device: {props.name} | capability sm_{props.major}{props.minor} | total {mb(total):.0f} MiB | free {mb(free):.0f} MiB")
    print(f"compiled arch list: {torch.cuda.get_arch_list()}")

    # tensor ops
    a = torch.arange(1_000_000, device=dev, dtype=torch.float32)
    s = (a * 2 + 1).sum().item()
    expect = 2 * (999_999 * 1_000_000 / 2) + 1_000_000
    ok_ops = abs(s - expect) / expect < 1e-5
    print(f"tensor ops: sum={s:.6g} expected={expect:.6g} -> {'ok' if ok_ops else 'FAIL'}")

    # matmul vs CPU
    torch.manual_seed(0)
    x, y = torch.randn(2048, 2048), torch.randn(2048, 2048)
    ref = x @ y
    xg, yg = x.to(dev), y.to(dev)
    torch.cuda.synchronize()
    t0 = time.perf_counter()
    for _ in range(20):
        zg = xg @ yg
    torch.cuda.synchronize()
    dt = (time.perf_counter() - t0) / 20
    err = (zg.cpu() - ref).abs().max().item()
    ok_mm = err < 1e-2
    print(f"matmul 2048x2048: {dt * 1e3:.2f} ms/iter on GPU, max abs diff vs CPU {err:.2e} -> {'ok' if ok_mm else 'FAIL'}")

    # autograd
    w = torch.randn(512, 512, device=dev, requires_grad=True)
    loss = (w @ w.T).pow(2).mean()
    loss.backward()
    ok_grad = bool(torch.isfinite(w.grad).all())
    print(f"autograd: grad finite -> {'ok' if ok_grad else 'FAIL'}")

    # memory allocate / release
    del a, xg, yg, zg, w, loss
    torch.cuda.empty_cache()
    base = torch.cuda.memory_allocated(0)
    block = torch.empty(1024, 1024, 256, device=dev)  # 1 GiB of float32
    block.fill_(1.0)
    held = torch.cuda.memory_allocated(0)
    peak = torch.cuda.max_memory_allocated(0)
    del block
    torch.cuda.empty_cache()
    after = torch.cuda.memory_allocated(0)
    reserved = torch.cuda.memory_reserved(0)
    ok_mem = (held - base) >= 2**30 and after <= base and reserved < 64 * 2**20
    print(f"memory: +{mb(held - base):.0f} MiB while held, peak {mb(peak):.0f} MiB, "
          f"after release allocated {mb(after):.0f} MiB reserved {mb(reserved):.0f} MiB -> {'ok' if ok_mem else 'FAIL'}")

    ok = ok_ops and ok_mm and ok_grad and ok_mem
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
