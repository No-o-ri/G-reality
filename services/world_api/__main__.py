"""Start the local companion API: .venv\\Scripts\\python -m world_api

Binds to loopback by default and prints a one-time pairing token that clients must send as
`Authorization: Bearer <token>`. Set GREALITY_TOKEN to pin the token (useful for scripted tests).
"""

from __future__ import annotations

import os

import uvicorn

from .app import create_app
from .config import get_settings


def main() -> None:
    settings = get_settings()
    app = create_app(settings, token=os.environ.get("GREALITY_TOKEN") or None)
    base = f"http://{settings.bind_host}:{settings.port}"
    print(f"G-reality companion on {base}  (interpreter: {settings.interpreter})")
    print(f"pairing token: {app.state.pairing_token}")
    print(f"allowed origins: {', '.join(settings.allowed_origins)}")
    uvicorn.run(app, host=settings.bind_host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
