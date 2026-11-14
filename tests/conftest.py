"""Shared fixtures. Database tests run against the Neon *dev* branch from .env.local and clean up after themselves."""

from __future__ import annotations

import uuid

import pytest

from world_api.config import get_settings
from world_api.interpreter import RulesInterpreter

FOREST_PROMPT = "a dense pine forest on rolling hills with a few boulders"


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
def pool(settings):
    if not settings.has_database:
        pytest.skip("DATABASE_URL not configured")
    from world_api.service import make_pool

    p = make_pool(settings.database_url)
    yield p
    p.close()


@pytest.fixture()
def service(pool):
    from world_api.service import WorldService

    return WorldService(pool, RulesInterpreter())


@pytest.fixture()
def world(service):
    """A fresh deterministic test world, deleted afterwards."""
    w = service.create_world(FOREST_PROMPT, seed=1234, world_id=f"test-{uuid.uuid4().hex[:10]}")
    yield w
    service.delete_world(w.world_id)
