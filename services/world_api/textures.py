"""CC0 photo textures for materials: found online on demand, bound to a world once, cached in Neon on first use.

Policy (G-reality uses only free, CC0 resources):
- The only source is Poly Haven (https://polyhaven.com/license: every asset is CC0). Search and downloads go to its
  public API and CDN (api.polyhaven.com, dl.polyhaven.org); nothing else is ever fetched, so model output cannot steer
  the server to other hosts.
- Nothing is pre-downloaded. When a world or creation is made, each material's search phrase (written by the language
  model) is matched against the live catalogue and the result is stored in the world spec (MaterialDefinition.texture):
  the asset id and parameters are fixed from then on. The three 1K maps of an asset are downloaded the first time any
  world needs them and kept in Neon (texture_assets / texture_maps); later requests are served from Neon.
- Rendering never waits for this: the browser keeps its procedural surfaces until the maps arrive.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.request
from typing import Optional

from psycopg.types.json import Jsonb

from greality_contracts import GeometryDefinition, MaterialDefinition, TextureBinding, WorldSpec

API = "https://api.polyhaven.com"
CDN_PREFIX = "https://dl.polyhaven.org/"
USER_AGENT = "G-reality/1.0 (+https://github.com/No-o-ri/G-reality)"
RESOLUTION = "1k"
MAPS = {"albedo": "Diffuse", "normal": "nor_gl", "arm": "arm", "height": "Displacement"}  # ours -> Poly Haven key
MAX_MAP_BYTES = 12 * 1024 * 1024
CATALOG_TTL_S = 6 * 3600
ASSET_RE = re.compile(r"^polyhaven:([a-z0-9_]{1,64})$")

_catalog: dict = {}
_catalog_at = 0.0
_lock = threading.Lock()


class TextureError(Exception):
    pass


def _get_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def catalog(timeout: float = 15.0) -> dict:
    """The live Poly Haven texture catalogue (slug -> info), fetched on first use and kept in memory for a few hours."""
    global _catalog, _catalog_at
    with _lock:
        if _catalog and time.time() - _catalog_at < CATALOG_TTL_S:
            return _catalog
    data = _get_json(f"{API}/assets?t=textures", timeout)
    with _lock:
        _catalog, _catalog_at = data, time.time()
    return data


_PATTERNS = {"tile", "brick", "plank", "wall", "paving", "pavement", "herringbone", "parquet", "cobblestone", "roof",
             "slab", "siding", "inlaid", "mosaic"}
_ADJECTIVES = {"dark", "black", "white", "red", "green", "blue", "grey", "gray", "brown", "yellow", "light", "pale",
               "rough", "smooth", "wet", "dry", "old", "weathered", "worn", "mossy", "cracked", "polished", "fresh",
               "ancient", "glowing", "shiny", "dirty", "clean", "large", "small", "fine", "coarse", "golden", "purple"}
_STOP = {"a", "an", "the", "of", "with", "and", "texture", "surface", "material", "pbr", "photo", "seamless"}


def _tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z]+", text.lower()):
        if w in _STOP or len(w) < 3:
            continue
        out.append(w[:-1] if w.endswith("s") and len(w) > 4 else w)
    return out


def search(query: str, cat: Optional[dict] = None) -> Optional[tuple[str, dict]]:
    """Best catalogue match for a short surface description, or None. Name words weigh most, then tags, then
    categories; every query word that matches nothing costs a point, so 'mossy granite' prefers a mossy rock."""
    words = _tokens(query)
    if not words:
        return None
    cat = cat if cat is not None else catalog()
    best, best_score = None, 0.0
    for slug, info in cat.items():
        name = set(_tokens(info.get("name", "") + " " + slug.replace("_", " ")))
        tags = set(_tokens(" ".join(info.get("tags", []))))
        cats = set(_tokens(" ".join(info.get("categories", []))))
        score = 0.0
        for w in words:
            hit = 3.0 if w in name else 2.0 if w in tags else 1.0 if w in cats else -0.5
            score += hit
        score += 0.3 * (words[0] in name)  # the head noun usually comes first ("bark pine" vs "pine bark")
        # laid patterns (tiles, bricks, planks, paving) only when asked for: natural surfaces must not look tiled
        score -= 4.0 * len((name & _PATTERNS) - set(words))
        if not any(w in name or w in tags for w in words if w not in _ADJECTIVES):
            continue  # a colour or adjective alone ('dark', 'rough') is not a match
        if score > best_score or (score == best_score and best and info.get("date_published", 0) >
                                  cat[best].get("date_published", 0)):
            best, best_score = slug, score
    if best is None or best_score < 2.0:
        return None
    return best, cat[best]


# generic surface classes, tried when a specific phrase ("dark volcanic basalt") has no CC0 match
_FALLBACK = (
    (("basalt", "obsidian", "granite", "stone", "rock", "cliff", "boulder", "slate", "lava", "volcanic", "crystal",
      "marble", "limestone", "sandstone"), "rock face"),
    (("ground", "soil", "dirt", "earth", "floor", "path", "mud", "swamp", "moss"), "forest ground"),
    (("sand", "dune", "desert", "beach"), "beach sand"),
    (("snow", "ice", "frost", "glacier"), "snow"),
    (("grass", "meadow", "lawn", "field"), "grass ground"),
    (("wood", "plank", "timber", "bark", "log", "cabin"), "wood planks"),
)


def _candidates(query: str) -> list[str]:
    words = query.split()
    out = [query]
    out += [" ".join(words[i:]) for i in range(1, len(words))]  # drop leading adjectives first
    low = query.lower()
    out += [generic for keys, generic in _FALLBACK if any(k in low for k in keys)]
    return list(dict.fromkeys(q for q in out if q.strip()))


def bind(query: str, cat: dict) -> TextureBinding:
    """Resolve one search phrase to a binding (asset_id stays None when nothing fits): the phrase itself, then shorter
    versions of it, then a generic surface class of the same kind."""
    query = query.strip()[:80]
    found = None
    for candidate in _candidates(query):
        found = search(candidate, cat)
        if found is not None:
            break
    if found is None:
        return TextureBinding(query=query)
    slug, info = found
    dims = info.get("dimensions") or [2000, 2000]
    size = max(0.2, min(40.0, float(dims[0]) / 1000.0))  # Poly Haven dimensions are millimetres
    return TextureBinding(query=query, asset_id=f"polyhaven:{slug}", name=str(info.get("name", slug))[:80],
                          page_url=f"https://polyhaven.com/a/{slug}", authors=list(info.get("authors", {}))[:6],
                          scale_m=size)


def _bind_materials(mats: list[MaterialDefinition], cat: dict, memo: dict) -> list[MaterialDefinition]:
    out = []
    for m in mats:
        if m.texture is not None and m.texture.asset_id is None:
            q = m.texture.query
            if q not in memo:
                memo[q] = bind(q, cat)
            b = memo[q]
            m = m.model_copy(update={"texture": b if b.asset_id else None})
        out.append(m)
    return out


def resolve_spec(spec: WorldSpec, notes: list[str]) -> WorldSpec:
    """Bind every pending texture query in a new world's spec (terrain and object materials). On any network failure
    the queries are dropped and the world keeps procedural surfaces; the notes say so."""
    pending = [m for m in _materials(spec) if m.texture is not None and m.texture.asset_id is None]
    if not pending:
        return spec
    try:
        cat = catalog()
    except Exception as exc:  # offline, timeout, API change: never block world creation on textures
        notes.append(f"photo textures unavailable ({type(exc).__name__}); procedural surfaces used")
        return _strip_pending(spec)
    memo: dict = {}
    terrain = spec.terrain.model_copy(update={
        k: _bind_materials([getattr(spec.terrain, k)], cat, memo)[0]
        for k in ("ground_material", "slope_material", "peak_material") if getattr(spec.terrain, k) is not None})
    archetypes = [a.model_copy(update={"materials": _bind_materials(a.materials, cat, memo)}) for a in spec.archetypes]
    for q, b in memo.items():
        notes.append(f"texture '{q}': {b.name} (CC0, Poly Haven)" if b.asset_id else f"texture '{q}': no CC0 match")
    return spec.model_copy(update={"terrain": terrain, "archetypes": archetypes})


def resolve_archetypes(geoms: list[GeometryDefinition], notes: list[str]) -> list[GeometryDefinition]:
    """Same for the archetypes of an in-world creation."""
    if not any(m.texture is not None and m.texture.asset_id is None for g in geoms for m in g.materials):
        return geoms
    try:
        cat = catalog()
    except Exception as exc:
        notes.append(f"photo textures unavailable ({type(exc).__name__})")
        return [g.model_copy(update={"materials": [m.model_copy(update={"texture": None}) if m.texture and
                                                   not m.texture.asset_id else m for m in g.materials]}) for g in geoms]
    memo: dict = {}
    return [g.model_copy(update={"materials": _bind_materials(g.materials, cat, memo)}) for g in geoms]


def _materials(spec: WorldSpec) -> list[MaterialDefinition]:
    t = spec.terrain
    out = [t.ground_material, t.slope_material] + ([t.peak_material] if t.peak_material else [])
    return out + [m for a in spec.archetypes for m in a.materials]


def _strip_pending(spec: WorldSpec) -> WorldSpec:
    def clean(m):
        return m.model_copy(update={"texture": None}) if m.texture and not m.texture.asset_id else m
    t = spec.terrain
    terrain = t.model_copy(update={k: clean(getattr(t, k)) for k in ("ground_material", "slope_material", "peak_material")
                                   if getattr(t, k) is not None})
    return spec.model_copy(update={"terrain": terrain, "archetypes": [
        a.model_copy(update={"materials": [clean(m) for m in a.materials]}) for a in spec.archetypes]})


def asset_ids(spec: WorldSpec) -> list[str]:
    return sorted({m.texture.asset_id for m in _materials(spec) if m.texture and m.texture.asset_id})


# ---------------------------------------------------------------------------------------------- cache (Neon)

def get_map(pool, asset_id: str, map_name: str, timeout: float = 30.0) -> tuple[bytes, str]:
    """(bytes, mime) of one map: from Neon if cached, otherwise downloaded from Poly Haven and stored first."""
    m = ASSET_RE.match(asset_id or "")
    if not m or map_name not in MAPS:
        raise TextureError("unknown texture")
    with pool.connection() as conn:
        row = conn.execute("SELECT data, mime FROM texture_maps WHERE asset_id=%s AND map=%s",
                           (asset_id, map_name)).fetchone()
    if row:
        return bytes(row[0]), row[1]
    slug = m.group(1)
    info = catalog().get(slug)
    if info is None:  # only assets in the CC0 catalogue
        raise TextureError("not in the Poly Haven catalogue")
    files = _get_json(f"{API}/files/{slug}", timeout)
    try:
        entry = files[MAPS[map_name]][RESOLUTION]["jpg"]
    except (KeyError, TypeError):
        raise TextureError(f"{asset_id} has no {map_name} map") from None
    url = str(entry["url"])
    if not url.startswith(CDN_PREFIX):
        raise TextureError("unexpected download host")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(MAX_MAP_BYTES + 1)
    if len(data) > MAX_MAP_BYTES or data[:3] != b"\xff\xd8\xff":
        raise TextureError("download is not a JPEG of the expected size")
    with pool.connection() as conn, conn.transaction():
        conn.execute(
            """INSERT INTO texture_assets (asset_id, source, license, name, page_url, authors)
               VALUES (%s, 'polyhaven', 'CC0-1.0', %s, %s, %s) ON CONFLICT DO NOTHING""",
            (asset_id, str(info.get("name", slug))[:80], f"https://polyhaven.com/a/{slug}",
             Jsonb(list(info.get("authors", {}))[:6])))
        conn.execute(
            """INSERT INTO texture_maps (asset_id, map, resolution, mime, sha256, source_url, data)
               VALUES (%s,%s,%s,'image/jpeg',%s,%s,%s) ON CONFLICT DO NOTHING""",
            (asset_id, map_name, RESOLUTION, hashlib.sha256(data).hexdigest(), url, data))
    return data, "image/jpeg"


def warm(pool, ids: list[str]) -> None:
    """Fetch the maps of a new world's textures into Neon in the background (first use), so its first opening does
    not wait on Poly Haven. Failures are ignored: the browser asks again and falls back to procedural surfaces."""
    def run():
        for asset in ids:
            for name in MAPS:
                try:
                    get_map(pool, asset, name)
                except Exception:
                    pass
    if ids:
        threading.Thread(target=run, daemon=True, name="texture-warm").start()
