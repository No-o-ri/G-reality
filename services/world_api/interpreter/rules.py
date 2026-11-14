"""Deterministic keyword interpreter: offline fallback and the fixture provider for automated tests."""

from __future__ import annotations

import re

from ..worldspec import FeatureDraft, WorldDraft
from .base import CommandContext, IntentDraft

_ACTION_WORDS = {
    "ignite": ("fire", "ignite", "burn", "torch", "light", "ablaze"),
    "extinguish": ("extinguish", "put out", "douse", "quench"),
    "remove": ("remove", "chop", "cut down", "fell", "destroy", "harvest", "clear", "break", "mine"),
    "build": ("build", "construct", "place", "make a"),
    "inspect": ("inspect", "look at", "examine", "what is", "check"),
}
_KINDS = {"tree": ("tree", "pine", "oak", "conifer"), "rock": ("rock", "boulder", "stone"),
          "house": ("house", "hut", "cabin", "building"), "campfire": ("campfire", "fire pit")}


def _has(text: str, *words: str) -> bool:
    return any(re.search(rf"\b{re.escape(w)}", text) for w in words)


class RulesInterpreter:
    name = "rules"
    kind = "rules"

    def world(self, prompt: str) -> WorldDraft:
        t = prompt.lower()
        relief, size, rugged = 40.0, 180.0, 0.25
        if _has(t, "mountain", "alpine", "peak", "cliff"):
            relief, size, rugged = 180.0, 320.0, 0.8
        elif _has(t, "hill", "rolling"):
            relief, size, rugged = 60.0, 200.0, 0.2
        elif _has(t, "flat", "plain", "meadow", "prairie", "desert"):
            relief, size, rugged = 12.0, 260.0, 0.05
        lake = _has(t, "lake", "pond", "lagoon", "water")
        ground = [0.20, 0.32, 0.14]
        if _has(t, "desert", "dune", "sand"):
            ground = [0.62, 0.50, 0.30]
        elif _has(t, "snow", "tundra", "winter", "arctic"):
            ground = [0.80, 0.83, 0.86]
        fog = 0.004
        if _has(t, "dense fog", "thick fog", "misty", "mist", "fog"):
            fog = 0.014 if _has(t, "dense", "thick") else 0.009
        humidity = 0.75 if _has(t, "rain", "wet", "swamp", "humid") else 0.15 if _has(t, "desert", "dry", "arid") else 0.4
        hour = 19.2 if _has(t, "sunset", "dusk", "evening") else 6.3 if _has(t, "dawn", "sunrise") else 10.0

        features: list[FeatureDraft] = []
        unsupported: list[str] = []
        if _has(t, "forest", "wood", "tree", "pine", "jungle"):
            dense = _has(t, "dense", "thick", "jungle")
            kind = "tree.broadleaf" if _has(t, "oak", "jungle", "broadleaf", "deciduous") else "tree.conifer"
            features.append(FeatureDraft(archetype=kind, placement="everywhere",
                                         density_per_hectare=140.0 if dense else 70.0))
        if _has(t, "rock", "boulder", "stone", "mountain"):
            features.append(FeatureDraft(archetype="rock.boulder", placement="everywhere", density_per_hectare=12.0))
        if _has(t, "village", "town", "settlement", "house", "cabin", "hamlet"):
            place = "near_spawn"
            if lake:
                place = "west_shore" if _has(t, "west") else "east_shore" if _has(t, "east") else "lakeshore"
            features.append(FeatureDraft(archetype="building.house", placement=place, count=7))
        if _has(t, "campfire", "camp"):
            features.append(FeatureDraft(archetype="prop.campfire", placement="near_spawn", count=1))
        for word in ("river", "ocean", "castle", "road", "bridge", "animal", "people", "city", "cave"):
            if _has(t, word):
                unsupported.append(f"{word} (no archetype in the current function library)")
        if not features:
            features.append(FeatureDraft(archetype="tree.conifer", placement="everywhere", density_per_hectare=35.0))

        title = " ".join(w.capitalize() for w in re.findall(r"[a-z]+", t)[:5]) or "Untitled world"
        return WorldDraft(
            title=title, relief_m=relief, feature_size_m=size, ruggedness=rugged, has_lake=lake,
            ground_color=ground, snow_peaks=_has(t, "snow", "alpine", "glacier"), time_of_day_h=hour,
            fog_density=fog, humidity=humidity, features=features, unsupported=unsupported,
        )

    def command(self, text: str, context: CommandContext) -> IntentDraft:
        t = text.lower()
        action = "unknown"
        # "put out the fire" must win over the word "fire".
        for name in ("extinguish", "build", "remove", "inspect", "ignite"):
            if _has(t, *_ACTION_WORDS[name]):
                action = name
                break
        kind = next((k for k, words in _KINDS.items() if _has(t, *words)), None)
        if action == "extinguish" and kind == "campfire" and "campfire" not in t:
            kind = None
        return IntentDraft(action=action, target_kind=kind, confidence=0.9 if action != "unknown" else 0.0)
