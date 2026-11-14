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

WORLD_SYSTEM = """You design explorable 3D worlds for the G-reality engine.
Return only JSON for the given schema. You choose terrain and climate parameters and which features to place.
Available feature archetypes: tree.conifer, tree.broadleaf, rock.boulder, building.house, prop.campfire.
Placements: everywhere, lowlands, highlands, lakeshore, east_shore, west_shore, near_spawn (shore placements need has_lake).
Use density_per_hectare (5-200) for scattered features such as trees and rocks, and count (1-40) for discrete
ones such as houses. Anything the user asks for that these parameters cannot express (rivers, animals, roads,
castles, ...) must be listed in `unsupported` instead of being approximated silently."""

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

    def _generate(self, system: str, user: str, schema: type[T], temperature: float, models: list[str]) -> T:
        from google.genai import errors

        config = self._types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_schema=schema,
            temperature=temperature,
            max_output_tokens=2048,
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
        return self._generate(WORLD_SYSTEM, prompt[:2000], WorldDraft, 0.7, self._models(WORLD_MODELS))

    def command(self, text: str, context: CommandContext) -> IntentDraft:
        user = (
            f"Command: {text[:500]}\n"
            f"Kind under the crosshair: {context.aimed_kind or 'none'}\n"
            f"Kinds nearby: {', '.join(context.nearby_kinds) or 'none'}"
        )
        return self._generate(COMMAND_SYSTEM, user, IntentDraft, 0.0, self._models(COMMAND_MODELS))
