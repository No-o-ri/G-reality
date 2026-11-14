"""HTTP surface (v1) of the world API. Local companion: loopback bind, bearer pairing token, origin allow-list."""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from greality_contracts import (
    CONTRACT_VERSION,
    ApiError,
    HealthReport,
    MutationResult,
    NeuralGenerationJob,
    PlayerState,
    RegionCoord,
    RegionSnapshot,
    WorldCommand,
    WorldEvent,
    WorldState,
)

from . import SERVICE_VERSION
from .config import Settings, get_settings
from .interpreter import InterpreterError, make_interpreter
from .service import NotFound, WorldService, make_pool


class CreateWorldRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    seed: Optional[int] = Field(default=None, ge=0, le=2**31 - 1)
    name: Optional[str] = Field(default=None, max_length=80)


class RegionBatchRequest(BaseModel):
    coords: list[RegionCoord] = Field(min_length=1, max_length=49)


def create_app(settings: Optional[Settings] = None, service: Optional[WorldService] = None,
               token: Optional[str] = None) -> FastAPI:
    settings = settings or get_settings()
    token = token or secrets.token_urlsafe(32)
    state: dict = {"service": service}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if state["service"] is None:
            if not settings.has_database:
                raise RuntimeError("DATABASE_URL is not configured in .env.local")
            state["pool"] = make_pool(settings.database_url)
            state["service"] = WorldService(state["pool"], make_interpreter(settings))
        yield
        if "pool" in state:
            state["pool"].close()

    app = FastAPI(title="G-reality world API", version=SERVICE_VERSION, lifespan=lifespan)
    app.state.pairing_token = token
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),  # explicit list, never "*"
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def origin_and_private_network(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin not in settings.allowed_origins:
            return JSONResponse(status_code=403, content=ApiError(
                code="origin_not_allowed", message="This origin is not allowed to call the companion.").model_dump())
        response = await call_next(request)
        if request.method == "OPTIONS" and origin:
            response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response

    def svc() -> WorldService:
        return state["service"]

    def auth(authorization: Optional[str] = Header(default=None)) -> None:
        expected = f"Bearer {token}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Missing or wrong pairing token."})

    @app.exception_handler(NotFound)
    async def not_found(_: Request, exc: NotFound):
        return JSONResponse(status_code=404, content=ApiError(code="not_found", message=str(exc)).model_dump())

    @app.exception_handler(InterpreterError)
    async def interpreter_failed(_: Request, exc: InterpreterError):
        status = 429 if exc.code == "rate_limited" else 504 if exc.code == "timeout" else 502
        return JSONResponse(status_code=status, content=ApiError(
            code=f"interpreter_{exc.code}", message=exc.message, detail={"retryable": exc.retryable}).model_dump())

    @app.get("/v1/health", response_model=HealthReport)
    def health() -> HealthReport:
        database = "unavailable"
        try:
            with svc().pool.connection(timeout=5) as conn:
                conn.execute("SELECT 1")
            database = "connected"
        except Exception:
            pass
        return HealthReport(service_version=SERVICE_VERSION, contract_version=CONTRACT_VERSION, database=database,
                            queue_depth=0)

    @app.post("/v1/worlds", response_model=WorldState, dependencies=[Depends(auth)])
    def create_world(body: CreateWorldRequest) -> WorldState:
        return svc().create_world(body.prompt, body.seed, body.name)

    @app.get("/v1/worlds", dependencies=[Depends(auth)])
    def list_worlds() -> list[dict]:
        return svc().list_worlds()

    @app.get("/v1/worlds/{world_id}", response_model=WorldState, dependencies=[Depends(auth)])
    def get_world(world_id: str) -> WorldState:
        return svc().get_world(world_id)

    @app.post("/v1/worlds/{world_id}/regions:batch", response_model=list[RegionSnapshot], dependencies=[Depends(auth)])
    def regions(world_id: str, body: RegionBatchRequest) -> list[RegionSnapshot]:
        return svc().load_regions(world_id, body.coords)

    @app.post("/v1/worlds/{world_id}/commands", response_model=MutationResult, dependencies=[Depends(auth)])
    def command(world_id: str, body: WorldCommand) -> MutationResult:
        if body.world_id != world_id:
            raise HTTPException(status_code=400, detail={"code": "world_mismatch", "message": "world_id differs from path"})
        return svc().handle_command(body)

    @app.get("/v1/worlds/{world_id}/events", response_model=list[WorldEvent], dependencies=[Depends(auth)])
    def events(world_id: str, after_revision: int = 0, limit: int = 200) -> list[WorldEvent]:
        return svc().events(world_id, after_revision, limit)

    @app.put("/v1/worlds/{world_id}/player", dependencies=[Depends(auth)])
    def put_player(world_id: str, body: PlayerState) -> dict:
        svc().update_player(world_id, body)
        return {"ok": True}

    @app.get("/v1/jobs", response_model=list[NeuralGenerationJob], dependencies=[Depends(auth)])
    def jobs(world_id: str) -> list[NeuralGenerationJob]:
        return svc().jobs(world_id)

    return app
