"""Gemini provider (official `google-genai` SDK) with structured output.

Safety properties:
- The model is asked for JSON matching WorldDraft / IntentDraft and the reply is re-validated here;
  anything else raises InterpreterError("invalid_response").
- Drafts only pick parameters and library archetypes. Nothing returned by the model is executed, and
  it cannot reference entity ids or state, so it cannot bypass the simulation.
- The API key is read from the environment and never logged or included in errors.
"""

from __future__ import annotations

import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..worldspec import WorldDraft
from .base import CommandContext, IntentDraft, InterpreterError

T = TypeVar("T", bound=BaseModel)

# Tried in order; a model that is overloaded (503), too slow (504/timeout) or retired (404) is skipped.
# Verified reachable with this project's key on 2026-11-14. GEMINI_MODEL, if set, is tried first.
WORLD_MODELS = ("gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.8-flash")
COMMAND_MODELS = ("gemini-3.5-flash-lite", "gemini-3.6-flash")
TIMEOUT_MS = 25_000
WORLD_TIMEOUT_MS = 120_000  # geometry-writing answers are long

WORLD_SYSTEM = """You design explorable 3D worlds for the G-reality engine. Return only JSON for the given schema.

1. Terrain and climate: the numeric fields (relief, ruggedness, lake, colors, time_of_day_h, fog_density,
   ambient_light: 0.03 very dark .. 0.25 daylight).
2. OBJECTS: for every distinct kind of thing the user describes (plants, mushrooms, crystals, arches, rings, statues,
   huts, ruins, trees, creatures-like sculptures, ...), CONSTRUCT its 3D shape in `objects` as a signed-distance
   geometry tree. Do not approximate requested things with unrelated shapes. `features` (legacy trees/rocks/houses)
   are optional background filler only.

GEOMETRY LANGUAGE. `geometry_json` is a JSON string holding ONE node: {"op": ..., "params": {...}, "children": [...],
"material": "<material id>"}. Local space: meters, origin at the object's base on the ground, +Y up. Keep each object
under 60 nodes and depth under 11. Numbers may be replaced by {"ref": "<parameter name>"} to vary per entity.
Primitives (no children):
  sphere {radius}; ellipsoid {radii:[x,y,z]}; box {half:[x,y,z]}; round_box {half:[x,y,z], radius};
  capsule {a:[x,y,z], b:[x,y,z], radius}   (a rounded straight segment: limbs, beams, struts);
  curve_tube {a, b, c, radius_start, radius_end} (a tube swept along a curve from a to c bending towards control point b,
    tapering: curved stems, arches, horns, tendrils, bent branches, ribs, drooping leaves);
  superquadric {radii:[x,y,z], exponent} (exponent 2 = ellipsoid, 4-10 = rounded box / pillow / slab,
    0.5-0.9 = pinched, faceted, crystal or star-like);
  cylinder {half_height, radius} (centred, Y axis); cone {half_height, radius_bottom, radius_top} (centred, Y axis);
  torus {major, minor} (ring in the XZ plane; rotate it to stand up); plane {normal:[x,y,z], offset}.
Combine (2..8 children unless noted): union; intersect; subtract (exactly 2: first minus second);
  smooth_union {k} (organic blending, k 0.05-2); smooth_subtract {k} (2); smooth_intersect {k} (2).
Domain (exactly 1 child): translate {offset:[x,y,z]}; rotate {quat:[x,y,z,w]} (e.g. 90 deg about X:
  [0.7071,0,0,0.7071]); scale {factor} (uniform); stretch {factor:[x,y,z]} (non-uniform);
  repeat {period:[x,y,z], count:[x,y,z]} (copies along axes, count 0 = off); radial_repeat {count, offset}
  (copies the child, placed at +X, around the Y axis: petals, gills, ribs, spokes, pillars in a ring);
  mirror {axis: 0|1|2}; twist {rate} (radians per meter about Y); bend {rate} (-1..1, bends along X);
  taper {rate} (-0.9..0.9, narrows or widens the cross-section with height); warp {amplitude, frequency, seed}
  (organic irregularity).
Fields (exactly 1 child): displace {amplitude, frequency, octaves 1-5, seed} (bumpy, eroded, crystalline-rough or soft
  surfaces); round {radius}; shell {thickness} (hollow).
Reuse: geometry_json may instead be {"defs": {"name": node, ...}, "root": node}; write {"use": "name"} anywhere to
insert a definition (e.g. define one blade, leaf, petal, rib or crystal once and place it several times).
Techniques: build curved forms from curve_tube (or several capsules, or a bent/tapered cylinder); caps and domes from a flattened
ellipsoid minus a box or smooth-subtracted; rings from torus (stretch it for asymmetry); arches from a torus intersected
with a half-space box or from a bent stretched box; crystals from stretched boxes/cones rotated and unioned, then
twisted; radial structure with radial_repeat. Raise objects that float with translate (e.g. y = 6).
Materials: every material id used in nodes must be listed in `materials`; glowing things get emissive_color and
emissive_strength 1.5-6 (they emit light in the renderer). Put the material on primitives or on a parent node.
PER-ENTITY VARIATION: declare 1-3 `parameters` (name, min, max) and use {"ref": name} in shape numbers (heights,
radii, bend/twist rates, cap sizes) so every placed entity is geometrically different.
Placement: density_per_hectare (2-150) for scattered objects or count (1-30) for large landmarks; size_m = radius of a
sphere around the base that contains the object at scale 1; placements: everywhere, lowlands, highlands, lakeshore,
east_shore, west_shore, near_spawn. flammable = true for plants and wooden things.
BEHAVIOUR. Give objects distinctive, fitting interactions composed from this closed grammar (no code):
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
Make behaviour fit the object: e.g. something fragile can shatter (set_condition removed + emit), something luminous
can flare and wake its neighbours (add_var emission + trigger_nearby), something growing can be harvested
(requires + add_var + scale_param + give + emit). Use requires to stop actions repeating forever.
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
            max_output_tokens=12000,
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

    def world(self, prompt: str) -> WorldDraft:
        draft = self._generate(WORLD_SYSTEM, prompt[:2000], WorldDraft, 0.7, self._models(WORLD_MODELS), WORLD_TIMEOUT_MS)
        # validator-driven repair: send the exact validation errors back once and replace the objects that failed
        from ..worldspec import object_geometry

        errors = {}
        for i, obj in enumerate(draft.objects[:8]):
            try:
                object_geometry(obj, i)
            except ValueError as exc:
                errors[i] = str(exc)[:400]
        if errors:
            listing = "\n".join(f"object {i} ({draft.objects[i].name}): {msg}" for i, msg in errors.items())
            fix = (f"Original request: {prompt[:1500]}\n\nYour previous answer: {draft.model_dump_json()[:9000]}\n\n"
                   f"These objects failed geometry validation:\n{listing}\n\nReturn the complete corrected JSON.")
            try:
                repaired = self._generate(WORLD_SYSTEM, fix, WorldDraft, 0.3, self._models(WORLD_MODELS), WORLD_TIMEOUT_MS)
                if len(repaired.objects) >= len(draft.objects) - len(errors):
                    draft = repaired
            except InterpreterError:
                pass  # keep the first answer; build_spec reports the rejected objects in the world's notes
        return draft

    def command(self, text: str, context: CommandContext) -> IntentDraft:
        user = (
            f"Command: {text[:500]}\n"
            f"Kind under the crosshair: {context.aimed_kind or 'none'}\n"
            f"Kinds nearby: {', '.join(context.nearby_kinds) or 'none'}"
        )
        return self._generate(COMMAND_SYSTEM, user, IntentDraft, 0.0, self._models(COMMAND_MODELS))
