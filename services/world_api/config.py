"""Runtime configuration. Secrets come from the git-ignored root `.env.local` (then `.env`), never from code."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILES = (".env.local", ".env")


def load_env_files(root: Path = ROOT) -> None:
    """Populate os.environ from KEY=VALUE files without overriding variables that are already set.

    GREALITY_ENV_FILE (set by the release launcher) names the secrets file explicitly; otherwise the repository's
    `.env.local` then `.env` are used."""
    explicit = os.environ.get("GREALITY_ENV_FILE")
    for path in ([Path(explicit)] if explicit else [root / name for name in ENV_FILES]):
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if value and key not in os.environ:
                os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    database_url_direct: str = field(repr=False)
    interpreter: str
    gemini_api_key: str = field(repr=False)
    gemini_model: str
    bind_host: str
    port: int
    allowed_origins: tuple[str, ...]
    web_dir: str = ""  # built frontend served at / (release mode)
    gpu_worker: bool = False  # run queued neural far-field jobs in the background (needs WSL2 GPU venv)

    @property
    def has_database(self) -> bool:
        return bool(self.database_url)


def get_settings() -> Settings:
    load_env_files()
    env = os.environ.get
    origins = tuple(o.strip() for o in env("GREALITY_ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o.strip())
    return Settings(
        database_url=env("DATABASE_URL", ""),
        database_url_direct=env("DATABASE_URL_DIRECT", "") or env("DATABASE_URL", ""),
        interpreter=env("GREALITY_INTERPRETER", "rules"),
        gemini_api_key=env("GEMINI_API_KEY", ""),
        gemini_model=env("GEMINI_MODEL", ""),
        bind_host=env("GREALITY_BIND_HOST", "127.0.0.1"),
        port=int(env("GREALITY_PORT", "8765")),
        allowed_origins=origins,
        web_dir=env("GREALITY_WEB_DIR", ""),
        gpu_worker=env("GREALITY_GPU_WORKER", "0") == "1",
    )
