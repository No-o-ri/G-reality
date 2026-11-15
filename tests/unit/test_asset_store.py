"""Local AssetStore: key validation, atomic writes, integrity check on read."""

from __future__ import annotations

import hashlib

import pytest

from world_api.assets import AssetIntegrityError, LocalAssetStore, asset_key


def test_round_trip_and_integrity(tmp_path):
    store = LocalAssetStore(tmp_path)
    data = b"ply\nfake bytes"
    sha = hashlib.sha256(data).hexdigest()
    key = asset_key("w-1", -3, 2, 7, sha)
    assert key == f"worlds/w-1/r_-3_2/rev7-{sha[:12]}.ply"
    store.put(key, data)
    assert store.exists(key) and store.get(key, sha) == data
    assert not list(tmp_path.rglob("*.tmp"))
    store.path(key).write_bytes(b"tampered")
    with pytest.raises(AssetIntegrityError):
        store.get(key, sha)
    store.delete(key)
    assert not store.exists(key)


@pytest.mark.parametrize("key", ["../x.ply", "a/../../x.ply", "/abs.ply", "a//b.ply", "a/./b.ply", "C:/x.ply", "a\\b.ply", "a b.ply", ""])
def test_rejects_unsafe_keys(tmp_path, key):
    with pytest.raises(ValueError):
        LocalAssetStore(tmp_path).path(key)
