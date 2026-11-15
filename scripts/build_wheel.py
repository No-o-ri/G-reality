r"""Build release\greality-<version>-py3-none-any.whl from the staged Windows release (run scripts\build_release.ps1 first).

The wheel holds the `greality` launcher package (packaging/greality) and, under greality/app, the same staged `app`
directory the ZIP ships (already secret-scanned by build_release.ps1). Installed with pip and started with
`py -m greality`, it runs entirely in the signed Python interpreter, which Smart App Control allows.

Usage: .venv\Scripts\python scripts\build_wheel.py
"""

from __future__ import annotations

import base64
import hashlib
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGE_APP = ROOT / "release" / "G-reality-win64" / "app"
LAUNCHER = ROOT / "packaging" / "greality"
SECRET = re.compile(rb"postgres(ql)?://[^\s'\"]*@|npg_[A-Za-z0-9]{8,}|AIza[0-9A-Za-z_\-]{20,}")


def local_secret_values() -> list[bytes]:
    values = []
    for name in (".env.local", ".env"):
        path = ROOT / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*['\"]?(.*?)['\"]?\s*$", line)
            if m and re.search("URL|KEY|TOKEN|SECRET|PASSWORD", m.group(1)) and len(m.group(2)) >= 12 \
                    and not re.match(r"^https?://(127\.0\.0\.1|localhost)", m.group(2)):
                values.append(m.group(2).encode())
    return values


def main() -> int:
    version = re.search(r'__version__ = "([^"]+)"', (LAUNCHER / "__init__.py").read_text()).group(1)
    if not (STAGE_APP / "web" / "index.html").is_file():
        print("Missing staged release: run scripts\\build_release.ps1 first.", file=sys.stderr)
        return 1
    requirements = [r.strip() for r in (ROOT / "packaging" / "requirements-runtime.txt").read_text().splitlines()
                    if r.strip() and not r.startswith("#")]
    files: list[tuple[str, bytes]] = []
    for path in sorted(LAUNCHER.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            files.append((f"greality/{path.relative_to(LAUNCHER).as_posix()}", path.read_bytes()))
    for path in sorted(STAGE_APP.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            files.append((f"greality/app/{path.relative_to(STAGE_APP).as_posix()}", path.read_bytes()))

    values = local_secret_values()
    for name, data in files:
        if Path(name).name in (".env", ".env.local") or SECRET.search(data) or any(v in data for v in values):
            print(f"Secret scan failed in {name}. Contents suppressed.", file=sys.stderr)
            return 1
    print(f"Secret scan PASS ({len(files)} files, {len(values)} local secret values checked).")

    dist = f"greality-{version}.dist-info"
    metadata = "\n".join(["Metadata-Version: 2.1", "Name: greality", f"Version: {version}",
                          "Summary: G-reality local companion (persistent, mathematically generated 3D worlds)",
                          "Requires-Python: >=3.12", *[f"Requires-Dist: {r}" for r in requirements], ""])
    files.append((f"{dist}/METADATA", metadata.encode()))
    files.append((f"{dist}/WHEEL", b"Wheel-Version: 1.0\nGenerator: g-reality build_wheel.py\nRoot-Is-Purelib: true\nTag: py3-none-any\n"))
    record = []
    for name, data in files:
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        record.append(f"{name},sha256={digest},{len(data)}")
    record.append(f"{dist}/RECORD,,")
    files.append((f"{dist}/RECORD", ("\n".join(record) + "\n").encode()))

    out = ROOT / "release" / f"greality-{version}-py3-none-any.whl"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as whl:
        for name, data in files:
            whl.writestr(zipfile.ZipInfo(name, date_time=(2026, 11, 15, 0, 0, 0)), data, zipfile.ZIP_DEFLATED)
    print(f"Wheel: {out} ({out.stat().st_size} bytes, {len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
