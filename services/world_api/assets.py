"""`AssetStore` (docs/architecture §12): bytes of derived neural assets live outside Postgres, addressed by key.

Keys look like `worlds/<world_id>/r_<rx>_<rz>/rev<revision>-<sha12>.ply`. The local implementation stores them under
`GREALITY_ASSET_DIR` (default `<repo>/data/assets`, git-ignored). Writes are atomic (temp file + rename) and reads
verify the sha256 recorded in the manifest, so a truncated or replaced file is never served.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from .config import ROOT

_KEY = re.compile(r"^[A-Za-z0-9_.\-]+(/[A-Za-z0-9_.\-]+)*$")


class AssetIntegrityError(RuntimeError):
    pass


def asset_key(world_id: str, rx: int, rz: int, region_revision: int, sha256: str, fmt: str = "ply") -> str:
    return f"worlds/{world_id}/r_{rx}_{rz}/rev{region_revision}-{sha256[:12]}.{fmt}"


class LocalAssetStore:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root or os.environ.get("GREALITY_ASSET_DIR") or ROOT / "data" / "assets").resolve()

    def path(self, key: str) -> Path:
        if not _KEY.match(key) or any(part in ("", ".", "..") for part in key.split("/")):
            raise ValueError(f"invalid asset key {key!r}")
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError(f"asset key escapes the store: {key!r}")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    def get(self, key: str, sha256: str | None = None) -> bytes:
        data = self.path(key).read_bytes()
        if sha256 is not None and hashlib.sha256(data).hexdigest() != sha256:
            raise AssetIntegrityError(f"asset {key} does not match its recorded sha256")
        return data

    def delete(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self.path(key).is_file()
