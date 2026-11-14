"""Language-provider abstraction.

A provider turns text into *drafts* (WorldDraft, IntentDraft): small, flat, schema-validated data.
It never produces code and never decides outcomes; world construction (worldspec.build_spec) and the
simulation (actions.py) are the only authorities. Swap providers via GREALITY_INTERPRETER.
"""

from __future__ import annotations

from typing import Literal, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..worldspec import WorldDraft

ActionName = Literal["ignite", "extinguish", "remove", "build", "inspect", "unknown"]


class IntentDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")
    action: ActionName
    target_kind: Optional[str] = Field(default=None, description="tree, rock, house, campfire, or null for 'this'")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class CommandContext(BaseModel):
    """What the interpreter may know about the situation. No ids, no state it could tamper with."""

    nearby_kinds: list[str] = Field(default_factory=list)
    aimed_kind: Optional[str] = None


class InterpreterError(Exception):
    """Provider failure with a stable code: timeout, rate_limited, unavailable, invalid_response, misconfigured."""

    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class Interpreter(Protocol):
    name: str
    kind: Literal["llm", "rules"]

    def world(self, prompt: str) -> WorldDraft: ...

    def command(self, text: str, context: CommandContext) -> IntentDraft: ...
