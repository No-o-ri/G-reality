"""Gemini provider tests.

Deterministic part: the SDK client is replaced by a stub that replays recorded/canned responses, so
parsing, validation, fallback and error mapping are tested without the network.
Live part (`-m gemini`): one real world request and a few real command requests.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.genai import errors

from world_api.interpreter import CommandContext, InterpreterError
from world_api.interpreter.gemini import COMMAND_MODELS, WORLD_MODELS, GeminiInterpreter
from world_api.worldspec import build_spec

FIXTURE = (Path(__file__).resolve().parents[1] / "fixtures" / "gemini_world_draft.json").read_text(encoding="utf-8")


class StubModels:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[str] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append(model)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return SimpleNamespace(text=step)


def stubbed(script) -> tuple[GeminiInterpreter, StubModels]:
    it = GeminiInterpreter("test-key-not-real")
    stub = StubModels(script)
    it._client = SimpleNamespace(models=stub)
    return it, stub


def api_error(code: int) -> errors.APIError:
    return errors.APIError(code, {"error": {"code": code, "message": "stub", "status": "STUB"}})


def test_recorded_world_response_builds_a_valid_spec():
    it, stub = stubbed([FIXTURE])
    draft = it.world("mountain forest with a lake and a village, plus a river and deer")
    spec = build_spec(draft, "prompt", seed=7)
    assert stub.calls == [WORLD_MODELS[0]]
    assert spec.terrain.water_level is not None
    assert {f.geometry_id for f in spec.features} >= {"tree.conifer", "building.house"}
    assert any("river" in n for n in spec.interpretation_notes)
    assert 0.0 <= spec.environment.fog_density <= 0.2


def test_out_of_schema_reply_is_rejected_then_next_model_is_used():
    bad = json.dumps({"title": "x", "relief_m": "very tall", "features": [{"archetype": "dragon.lair"}]})
    it, stub = stubbed([bad, FIXTURE])
    it.world("anything")
    assert stub.calls == list(WORLD_MODELS[:2]) and it.last_model == WORLD_MODELS[1]


def test_reply_cannot_smuggle_extra_fields_or_code():
    payload = json.loads(FIXTURE)
    payload["__import__"] = "os.system('calc')"
    payload["features"][0]["shader"] = "void main(){}"
    it, _ = stubbed([json.dumps(payload)])
    draft = it.world("anything")
    dumped = draft.model_dump_json()
    assert "os.system" not in dumped and "shader" not in dumped


def test_overloaded_and_slow_models_fall_through():
    it, stub = stubbed([api_error(503), api_error(504), '{"action":"ignite","target_kind":"tree","confidence":0.9}'])
    it.preferred = "custom-model"
    intent = it.command("burn it", CommandContext())
    assert intent.action == "ignite"
    assert stub.calls == ["custom-model", *COMMAND_MODELS]


def test_all_models_failing_raises_a_coded_error(monkeypatch):
    monkeypatch.setattr("world_api.interpreter.gemini.time.sleep", lambda _: None)
    it, _ = stubbed([api_error(429)] * len(COMMAND_MODELS))
    with pytest.raises(InterpreterError) as info:
        it.command("burn it", CommandContext())
    assert info.value.code == "rate_limited" and info.value.retryable
    it, _ = stubbed(["not json"] * len(COMMAND_MODELS))
    with pytest.raises(InterpreterError) as info:
        it.command("burn it", CommandContext())
    assert info.value.code == "invalid_response"


def test_bad_key_is_not_retried():
    it, stub = stubbed([api_error(403), FIXTURE])
    with pytest.raises(InterpreterError) as info:
        it.world("anything")
    assert info.value.code == "misconfigured" and len(stub.calls) == 1
    with pytest.raises(InterpreterError):
        GeminiInterpreter("")


def test_service_falls_back_to_rules_when_provider_fails():
    from world_api.service import WorldService

    it, _ = stubbed([api_error(503)] * len(COMMAND_MODELS))
    svc = WorldService(pool=None, interpreter=it)
    draft, kind, note = svc._interpret("set this tree on fire", CommandContext())
    assert (draft.action, kind) == ("ignite", "rules") and note.startswith("unavailable")


@pytest.mark.gemini
def test_live_gemini_world_and_commands(settings):
    if not settings.gemini_api_key:
        pytest.skip("GEMINI_API_KEY not configured")
    it = GeminiInterpreter(settings.gemini_api_key, settings.gemini_model)
    draft = it.world("A misty conifer forest around a lake with a small village on the eastern shore, and a dragon.")
    spec = build_spec(draft, "prompt", seed=3)
    assert spec.features and spec.terrain.water_level is not None
    print(f"\nlive world via {it.last_model}: {spec.title!r}, {len(spec.features)} features, notes={spec.interpretation_notes}")
    expected = {"Set this tree on fire.": "ignite", "please put out the flames": "extinguish",
                "chop down that pine": "remove", "recite a poem": "unknown"}
    for text, action in expected.items():
        intent = it.command(text, CommandContext(nearby_kinds=["tree", "rock"], aimed_kind="tree"))
        print(f"live command via {it.last_model}: {text!r} -> {intent.action}")
        assert intent.action == action
