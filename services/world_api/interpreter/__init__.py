"""Interpreter selection. `rules` is always available; `gemini` needs GEMINI_API_KEY."""

from __future__ import annotations

from ..config import Settings
from .base import CommandContext, IntentDraft, Interpreter, InterpreterError
from .rules import RulesInterpreter

__all__ = ["CommandContext", "IntentDraft", "Interpreter", "InterpreterError", "RulesInterpreter", "make_interpreter"]


def make_interpreter(settings: Settings) -> Interpreter:
    if settings.interpreter == "gemini":
        from .gemini import GeminiInterpreter

        return GeminiInterpreter(settings.gemini_api_key, settings.gemini_model)
    if settings.interpreter == "rules":
        return RulesInterpreter()
    raise InterpreterError("misconfigured", f"Unknown GREALITY_INTERPRETER '{settings.interpreter}' (use gemini or rules)")
