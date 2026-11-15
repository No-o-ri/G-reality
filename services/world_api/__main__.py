r"""Start the local companion API: .venv\Scripts\python -m world_api

Binds to loopback by default and prints a one-time pairing token that clients must send as
`Authorization: Bearer <token>`. Set GREALITY_TOKEN to pin the token (useful for scripted tests).

Release mode (packaging/launcher/launch.ps1): GREALITY_WEB_DIR serves the built frontend from the same origin,
GREALITY_OPEN_BROWSER=1 opens the default browser at the paired URL once the server is healthy, and
GREALITY_ENV_FILE selects the secrets file.
"""

from __future__ import annotations

import os
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from .app import create_app
from .config import get_settings


def _open_when_healthy(base: str, url: str) -> None:
    for _ in range(240):
        try:
            with urllib.request.urlopen(f"{base}/v1/health", timeout=2):
                webbrowser.open(url)
                return
        except Exception:
            time.sleep(0.5)


def main() -> None:
    settings = get_settings()
    app = create_app(settings, token=os.environ.get("GREALITY_TOKEN") or None)
    base = f"http://{settings.bind_host}:{settings.port}"
    print(f"G-reality companion on {base}  (interpreter: {settings.interpreter})", flush=True)
    print(f"pairing token: {app.state.pairing_token}", flush=True)
    print(f"allowed origins: {', '.join(settings.allowed_origins)}", flush=True)
    for origin in settings.allowed_origins:
        print(f"open: {origin}/#companion={base}&token={app.state.pairing_token}", flush=True)
    if settings.web_dir:
        play = f"{base}/#companion={base}&token={app.state.pairing_token}"
        print(f"G-reality is running. Play at: {play}", flush=True)
        print("Close this window or press Ctrl+C to stop.", flush=True)
        if os.environ.get("GREALITY_OPEN_BROWSER") == "1":
            threading.Thread(target=_open_when_healthy, args=(base, play), daemon=True).start()
    uvicorn.run(app, host=settings.bind_host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
