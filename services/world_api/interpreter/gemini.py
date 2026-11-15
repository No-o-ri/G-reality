"""Gemini provider (official `google-genai` SDK) with structured output.

Safety properties:
- The model is asked for JSON matching WorldDraft / CreationDraft / IntentDraft and the reply is re-validated here;
  anything else raises InterpreterError("invalid_response").
- Drafts only describe geometry as data in the closed op language and pick parameters. Nothing returned by the model is
  executed, and it cannot reference entity ids or state, so it cannot bypass the simulation.
- The API key is read from the environment and never logged or included in errors.
"""

from __future__ import annotations

import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..worldspec import CreationDraft, WorldDraft, draft_report
from .base import CommandContext, IntentDraft, InterpreterError

T = TypeVar("T", bound=BaseModel)

# Tried in order; a model that is overloaded (503), too slow (504/timeout) or retired (404) is skipped.
# Verified reachable with this project's key on 2026-11-14. GEMINI_MODEL, if set, is tried first.
# Geometry: measured 2026-11-14 on the same dreamscape prompt, flash-lite answered in 8 s with 2-5 nodes per object
# (6 validation/fidelity problems), 3.6-flash in 43 s with every requested structure built (0 problems), 3.8-flash
# exceeded 120 s. The lite model stays as a fallback; its answers then go through the repair round.
WORLD_MODELS = ("gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash")
COMMAND_MODELS = ("gemini-3.5-flash-lite", "gemini-3.6-flash")
TIMEOUT_MS = 25_000
WORLD_TIMEOUT_MS = 120_000  # geometry-writing answers are long

GEOMETRY_LANGUAGE = """GEOMETRY LANGUAGE. `geometry_json` is a JSON string holding ONE node: {"op": ..., "params": {...},
"children": [...], "material": "<material id>"}. Local space: meters, +Y up, origin = the object's base point on the
ground. Numbers may be replaced by {"ref": "<parameter name>"} to vary per entity.
Primitives (no children). CENTRED on the origin: sphere {radius}; ellipsoid {radii:[x,y,z]}; box {half:[x,y,z]};
  round_box {half, radius}; superquadric {radii, exponent} (2 = ellipsoid, 4-10 = rounded slab/pillow, 0.5-0.9 = pinched,
  faceted, crystal or star-like); cylinder {half_height, radius} (Y axis); cone {half_height, radius_bottom,
  radius_top} (Y axis, bottom at -half_height); torus {major, minor} (a ring LYING FLAT in the XZ plane);
  polyhedron {kind: 0 tetrahedron | 1 cube | 2 octahedron | 3 dodecahedron | 4 icosahedron, radius (centre to face)}.
  Not centred: capsule {a, b, radius} (rounded segment a->b); curve_tube {a, b, c, radius_start, radius_end} (tube
  swept along a curve from a to c bending towards b: arches, horns, tendrils, ribs, bent branches); plane {normal,
  offset} (only inside intersect/subtract).
Combine (2..8 children): union; intersect; subtract (exactly 2: first minus second); smooth_union {k} (organic blend,
  k 0.05-2); smooth_subtract {k} (2); smooth_intersect {k} (2).
Domain (exactly 1 child): translate {offset:[x,y,z]}; rotate {quat:[x,y,z,w]} (90 deg about X = [0.7071,0,0,0.7071],
  about Z = [0,0,0.7071,0.7071], about Y = [0,0.7071,0,0.7071], 45 deg about X = [0.3827,0,0,0.9239]); scale {factor};
  stretch {factor:[x,y,z]}; mirror {axis: 0|1|2}; repeat {period:[x,y,z], count:[x,y,z]} (count 0 = off);
  radial_repeat {count, offset} (copies of a child placed at +X around the Y axis: petals, ribs, spokes, pillars);
  spiral_repeat {count, pitch, copies} (copy j turned by j*360/count degrees about Y and raised by j*pitch/count, so
  pitch = rise per turn: spiral staircases, helical ridges, DNA-like ribbons, coiled tendrils; child placed at +X);
  twist {rate} (radians per meter about Y); bend {rate} (-1..1, along X); taper {rate} (-0.9..0.9, cross-section
  narrows (<0) or widens (>0) with height); warp {amplitude, frequency, seed} (organic irregularity).
Fields (exactly 1 child): displace {amplitude, frequency, octaves 1-3, seed} (rough, eroded, rocky surfaces);
  round {radius}; shell {thickness} (hollow).
Reuse: geometry_json may be {"defs": {"name": node, ...}, "root": node}; {"use": "name"} inserts a definition anywhere
  (define a blade, rib, step, shard or branch once and place it many times; definitions may use earlier ones).
Budget per object: at most 70 nodes after expanding definitions, depth 12, at most 3 displace/warp nodes.

CONVENTIONS (the most common mistakes):
- Centred primitives extend below the origin: anything standing on the ground must be translated up by its half height
  (a cylinder with half_height 10 standing on the ground needs translate [0, 10, 0]). Check the lowest point is y >= 0.
- A torus lies flat. Rings, hoops, gates and portals stand upright: rotate the torus 90 deg about X, then translate it
  up by major + minor so it touches the ground, or higher when it floats.
- Shape must match the noun: a ring must read as a ring, a spiral as a spiral, an island as an island. Colour and glow
  alone never make something recognizable. Give landmarks 12-60 nodes of real structure.

TECHNIQUES (compose them; vary proportions and details; they are methods, not finished models):
- Intersecting rings: one object, a union of several rotated tori sharing a centre (different quats), raised up.
- Twisted spiral tower: twist {rate 0.1-0.5} of a tall star-shaped cross-section (radial_repeat of a translated box
  around a core cylinder), optionally taper, translated up by half its height; or a core plus a spiral_repeat ridge.
- Spiral staircase: spiral_repeat {count 12-20, pitch 3-6, copies 20-80} of a step box translated to +X, around a
  central column; railings as curve_tube or a second spiral_repeat of thin posts.
- Floating island: a flattened, displaced top (cylinder or ellipsoid) smooth-unioned with an inverted rocky underside
  (cone with a small radius_bottom and large radius_top, displaced), perhaps small hanging rocks; raise it.
- Arch: curve_tube from [-w,0,0] through [0,2h,0] to [w,0,0]; or the upper half of an upright torus (intersect it with a
  box above y = 0); crystalline arches add shards (polyhedron, superquadric exponent 0.6) along it.
- Polyhedral sculptures: polyhedron, compounds (union with a rotated copy), hollow frames (shell, or subtract a
  smaller polyhedron and slots), nested shells.
- Branching / fractal forms: a branch as a definition; the next level = radial_repeat of rotated, scaled, translated uses
  of the previous one (2-3 levels within the budget); lattices with repeat inside intersect/subtract.
Materials: every material id used in nodes must be listed in `materials`; glowing things get emissive_color and
emissive_strength 1.5-6 (they emit light). Put the material on primitives or on a parent node.
PER-ENTITY VARIATION: declare 0-3 `parameters` (name, min, max) and use EACH of them as {"ref": name} in the geometry.
size_m = radius of a sphere around the base point that contains the object."""

BEHAVIOUR = """BEHAVIOUR. Give objects distinctive, fitting interactions composed from this closed grammar (no code):
  vars: initial state variables (name, value), e.g. emission 1 for glowing things, charge, spores, cracks.
  interactions[] (0-3 per object): {id, verb (prompt text, 1-3 words), range_m (1-8), player_initiated,
    requires: [{var, op, value} or {condition: intact|burning|charred|removed, op: "=="|"!="}],
    effects: [...], message (feedback sentence)}.
  effect types: set_var {var, value}; add_var {var, value (delta), min, max}; set_param / scale_param {param (one of
    the object's own `parameters`), value or factor, min, max} (changes its geometry); set_condition {condition:
    intact|charred|removed}; ignite (flammable objects catch fire; fire spreads by itself); emit {particles: sparks|
    spores|dust|smoke|embers|glow, color, count 10-200}; give {resource, amount}; trigger_nearby {radius_m <= 30,
    max_targets <= 8, same_kind, interaction (id of a player_initiated=false interaction of this object that runs on
    the neighbours)}. The variable `emission` scales an object's glow and emitted light (0 = dark, 2 = twice as bright).
Make behaviour fit the object: something fragile can shatter (set_condition removed + emit), something luminous can
flare and wake its neighbours (add_var emission + trigger_nearby), something growing can be harvested (requires +
add_var + scale_param + give + emit). Use requires to stop actions repeating forever. flammable = true for plants and
wooden things."""

WORLD_SYSTEM = f"""You design explorable 3D worlds for the G-reality engine. Return only JSON for the given schema.

1. Terrain and climate: the numeric fields (relief, ruggedness, lake, colors, time_of_day_h, fog_density,
   ambient_light: 0.03 very dark .. 0.25 daylight).
2. requested_structures: list EVERY distinct structure or object kind the user names (e.g. 'floating ring',
   'spiral tower', 'floating island', 'crystal arch', 'polyhedron', 'fractal tree').
3. OBJECTS: construct each requested structure as its own object (up to 8; merge only near-identical ones) as a
   signed-distance geometry tree, named with the user's noun. `features` (legacy trees/rocks/houses) are optional
   background filler only and never replace a requested structure.
   Placement: density_per_hectare (2-150) for scattered things or count (1-30) for landmarks; placements: everywhere,
   lowlands, highlands, lakeshore, east_shore, west_shore, near_spawn. Things that float are raised inside their
   geometry (translate the whole tree up).

{GEOMETRY_LANGUAGE}

{BEHAVIOUR}
List in `unsupported` only what truly cannot be built (moving creatures, liquids flowing, text)."""

CREATE_SYSTEM = f"""A player inside an existing G-reality world asks you to CREATE something new in it. Return only JSON
for the given schema. You construct new geometry; the existing world stays as it is.

- requested_structures: list every distinct structure the request names.
- objects (1-4): parts that touch or interlock form ONE object (a ring with three intersecting rings is one object);
  things that stand apart are separate objects (three islands connected by bridges = islands + bridges, or one object
  if compact). Name each object with the user's noun.
- Layout: objects are placed around an anchor on the ground in front of the player. forward_m / right_m offset each
  object from the anchor (keep the group within 60 m), height_m lifts an object's base above the ground (floating,
  hovering, levitating: 3-40 m), yaw_deg turns it, scale (0.2-5) resizes it. distance_m (optional) moves the anchor
  further away for very large things; 0 = automatic. Do not build ground, terrain or water.
- Size honours the words: small 1-3 m, large 6-15 m, enormous / gigantic / giant 20-60 m.

{GEOMETRY_LANGUAGE}

{BEHAVIOUR}
List in `unsupported` only what truly cannot be built (moving creatures, liquids flowing, text)."""

COMMAND_SYSTEM = """You translate a player's typed command in a 3D world into one structured intent.
Return only JSON for the given schema. Actions: ignite (set on fire), extinguish (put a fire out),
remove (chop, cut down, destroy, harvest, clear), build, inspect (look at, examine), unknown.
target_kind is one of tree, rock, house, campfire, or null when the player says 'this'/'it'.
You only classify the request. Whether it is allowed is decided elsewhere; never refuse or add commentary."""


class GeminiInterpreter:
    name = "gemini"
    kind = "llm"

    def __init__(self, api_key: str, model: str = ""):
        if not api_key:
            raise InterpreterError("misconfigured", "GEMINI_API_KEY is not set in .env.local")
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=TIMEOUT_MS))
        self.preferred = model
        self.last_model: str | None = None  # which model produced the most recent answer
        self.last_notes: list[str] = []  # what the most recent world/create call did (models, repair), for the notes

    def _models(self, defaults: tuple[str, ...]) -> list[str]:
        return list(dict.fromkeys(([self.preferred] if self.preferred else []) + list(defaults)))

    def _generate(self, system: str, user: str, schema: type[T], temperature: float, models: list[str],
                  timeout_ms: int | None = None) -> T:
        from google.genai import errors

        config = self._types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=temperature,
            max_output_tokens=16000,
            # per-request deadline: geometry-writing world answers are long, commands are short
            http_options=self._types.HttpOptions(timeout=timeout_ms) if timeout_ms else None,
        )
        last: InterpreterError | None = None
        for attempt, model in enumerate(models):
            try:
                response = self._client.models.generate_content(model=model, contents=user, config=config)
            except ValidationError as exc:  # the SDK rejected our request locally (e.g. unsupported schema type)
                raise InterpreterError(
                    "misconfigured", f"Gemini request could not be built ({exc.error_count()} schema problem(s)).") from None
            except errors.APIError as exc:  # HTTP-level failure from the service
                code = getattr(exc, "code", None)
                if code in (401, 403):
                    raise InterpreterError("misconfigured", f"Gemini rejected the API key or project ({code}).") from None
                if code == 429:
                    last = InterpreterError("rate_limited", "Gemini rate limit reached; try again shortly.", True)
                    time.sleep(1.5)
                elif code == 504:
                    last = InterpreterError("timeout", f"Gemini model {model} exceeded its deadline.", True)
                else:  # 5xx overload, 404 retired model, 400 unsupported feature for this model
                    last = InterpreterError("unavailable", f"Gemini model {model} unavailable ({code}).", True)
            except Exception as exc:  # timeouts and transport errors; the message never includes the key
                name = type(exc).__name__
                is_timeout = "timeout" in name.lower()
                last = InterpreterError("timeout" if is_timeout else "unavailable",
                                        f"Gemini request to {model} failed: {name}.", True)
            else:
                try:
                    parsed = schema.model_validate_json(response.text or "")
                except ValidationError as exc:
                    problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:3])
                    last = InterpreterError("invalid_response",
                                            f"Gemini returned data outside the schema ({problems}).", True)
                else:
                    self.last_model = model
                    return parsed
        assert last is not None
        raise last

    def _with_repair(self, system: str, request: str, first: T, schema: type[T], models: list[str]) -> T:
        """Validator- and measurement-driven repair: send the exact problems (failed validation, missing requested
        structures, buried or degenerate geometry, unused parameters) and the measured extents back once, and keep
        the answer with fewer hard problems."""
        hard, every = draft_report(first.objects, first.requested_structures)
        self.last_notes = [f"model {self.last_model}"]
        if not hard:
            return first
        fix = (f"Original request: {request[:1500]}\n\nYour previous answer: {first.model_dump_json()[:12000]}\n\n"
               f"Problems found when the engine validated and measured it:\n" + "\n".join(f"- {m}" for m in every[:40])
               + "\n\nReturn the complete corrected JSON. Keep what was right; fix every problem listed.")
        try:
            repaired = self._generate(system, fix, schema, 0.3, models, WORLD_TIMEOUT_MS)
        except InterpreterError as exc:
            self.last_notes.append(f"repair round failed ({exc.code}); kept the first answer")
            return first
        hard_after, _ = draft_report(repaired.objects, repaired.requested_structures)
        if len(hard_after) <= len(hard) and repaired.objects:
            self.last_notes.append(f"repair round fixed {len(hard) - len(hard_after)} of {len(hard)} problems")
            return repaired
        self.last_notes.append(f"repair round did not improve ({len(hard_after)} problems); kept the first answer")
        return first

    def world(self, prompt: str) -> WorldDraft:
        models = self._models(WORLD_MODELS)
        draft = self._generate(WORLD_SYSTEM, prompt[:2000], WorldDraft, 0.7, models, WORLD_TIMEOUT_MS)
        return self._with_repair(WORLD_SYSTEM, prompt, draft, WorldDraft, models)

    def create(self, text: str, context: CommandContext) -> CreationDraft:
        models = self._models(WORLD_MODELS)
        user = (f"Request: {text[:500]}\nWorld: {context.world_title or 'unnamed'}\n"
                f"Around the player: {context.terrain_summary or 'unknown terrain'}; nearby: "
                f"{', '.join(context.nearby_kinds) or 'nothing'}")
        draft = self._generate(CREATE_SYSTEM, user, CreationDraft, 0.6, models, WORLD_TIMEOUT_MS)
        return self._with_repair(CREATE_SYSTEM, user, draft, CreationDraft, models)

    def command(self, text: str, context: CommandContext) -> IntentDraft:
        user = (
            f"Command: {text[:500]}\n"
            f"Kind under the crosshair: {context.aimed_kind or 'none'}\n"
            f"Kinds nearby: {', '.join(context.nearby_kinds) or 'none'}"
        )
        return self._generate(COMMAND_SYSTEM, user, IntentDraft, 0.0, self._models(COMMAND_MODELS))
