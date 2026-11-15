r"""Start the G-reality companion from a pip installation:  py -m greality [--port N] [--env-file PATH] [--no-browser]
[--preflight-only] [--smoke-test] [--shortcut]

The pip counterpart of packaging/launcher/launch.ps1 for machines with Smart App Control, where scripts downloaded
from the internet (.cmd/.ps1) are blocked. Everything runs in the signed Python interpreter that installed the
package. The bundled `app` directory has the release layout (services, contracts, migrations, built web app), so the
server is started exactly as the ZIP launcher starts it.

Settings: %APPDATA%\G-reality\.env.local (asked for on first run, input hidden) or --env-file.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from . import __version__

APP = Path(__file__).resolve().parent / "app"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "G-reality"
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "G-reality"


def check(marker: str, message: str) -> None:
    print(f"[{marker}] {message}", flush=True)


def read_settings(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if not m:
            continue
        value = m.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = re.sub(r"\s+#.*$", "", value)
        result[m.group(1)] = value
    return result


def first_run_setup() -> Path:
    """Write %APPDATA%\\G-reality\\.env.local from hidden console input."""
    print()
    print("First-run setup. G-reality keeps worlds in your own PostgreSQL database; a free Neon project works:")
    print("  https://neon.tech -> New project -> Connect -> copy the connection string (postgresql://...).")
    print("Without a database, the read-only sample world still runs on the G-reality website (no install).")
    database = getpass.getpass("Paste DATABASE_URL (input hidden; Enter to cancel): ").strip()
    if not database:
        raise SystemExit("[--] Setup cancelled: a database URL is required to run the companion.")
    if not re.match(r"^postgres(ql)?://", database):
        raise SystemExit("[!!] That does not look like a PostgreSQL URL (postgresql://...).")
    gemini = getpass.getpass("Optional: paste a Gemini API key (input hidden; Enter to skip and use the keyword interpreter): ").strip()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = CONFIG_DIR / ".env.local"
    path.write_text(f"DATABASE_URL={database}\nGEMINI_API_KEY={gemini}\nGREALITY_INTERPRETER={'gemini' if gemini else 'rules'}\n",
                    encoding="utf-8")
    check("ok", f"Settings saved to {path} (edit or delete it to change them).")
    return path


def create_shortcut() -> None:
    """Desktop shortcut that runs this module with the same signed interpreter (created locally, so no web mark)."""
    # The shell's Desktop folder (may be redirected, e.g. to OneDrive).
    script = ("$p = Join-Path ([Environment]::GetFolderPath('Desktop')) 'G-reality.lnk'; "
              "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($p); $s.TargetPath = $env:GR_PY; "
              "$s.Arguments = '-m greality'; $s.WorkingDirectory = $env:USERPROFILE; $s.Description = 'G-reality'; $s.Save(); $p")
    env = {**os.environ, "GR_PY": sys.executable}
    done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], env=env,
                          capture_output=True, text=True)
    check("ok" if done.returncode == 0 else "!!",
          f"Desktop shortcut created: {done.stdout.strip()}" if done.returncode == 0 else "Desktop shortcut could not be created.")


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def smoke(port: int, token: str, server: subprocess.Popen) -> None:
    base = f"http://127.0.0.1:{port}"
    started = time.monotonic()
    while time.monotonic() - started < 90:
        if server.poll() is not None:
            raise RuntimeError("Server exited before becoming healthy.")
        try:
            with urllib.request.urlopen(f"{base}/v1/health", timeout=2) as r:
                if json.load(r).get("database") == "connected":
                    break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        raise RuntimeError("Database health did not become connected within 90 seconds.")
    health_s = time.monotonic() - started
    with urllib.request.urlopen(f"{base}/", timeout=10) as r:
        if r.status != 200 or b"G-reality" not in r.read():
            raise RuntimeError("Frontend check failed.")
    request = urllib.request.Request(f"{base}/v1/worlds", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=10) as r:
        if r.status != 200:
            raise RuntimeError("Authenticated worlds check failed.")
    print(f"Smoke test PASS: health {health_s:.1f}s.", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="py -m greality", description="Start the G-reality local companion.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--shortcut", action="store_true", help="create a desktop shortcut and exit")
    args = parser.parse_args(argv)

    if args.shortcut:
        create_shortcut()
        return 0
    print(f"G-reality {__version__} preflight", flush=True)
    if sys.version_info < (3, 12):
        check("!!", "Python 3.12+ required.")
        return 1
    check("ok", f"Python {sys.version.split()[0]}")
    if not (APP / "web" / "index.html").is_file():
        check("!!", f"The installed package is incomplete (missing {APP / 'web' / 'index.html'}). Reinstall it.")
        return 1

    if args.env_file:
        if not args.env_file.is_file():
            check("!!", "The specified secrets file does not exist.")
            return 1
        env_file = args.env_file.resolve()
    else:
        env_file = CONFIG_DIR / ".env.local"
        if not env_file.is_file():
            if args.smoke_test or args.preflight_only:
                check("!!", f"Secrets file missing: {env_file}")
                return 1
            env_file = first_run_setup()
    settings = read_settings(env_file)
    if not settings.get("DATABASE_URL", "").strip():
        check("!!", f"DATABASE_URL is empty. Fill it in {env_file}.")
        return 1
    check("ok", "Secrets file found; DATABASE_URL is present (value hidden).")
    has_key = bool(settings.get("GEMINI_API_KEY", "").strip())
    check("ok" if has_key else "--", "GEMINI_API_KEY is present (value hidden)." if has_key
          else "GEMINI_API_KEY empty: the keyword interpreter will be used (worlds still generate; less faithful to prompts)")
    if not port_free(args.port):
        check("!!", f"Port {args.port} is unavailable. Stop the other instance or use --port.")
        return 1
    check("ok", f"Port {args.port} is free.")
    if args.preflight_only:
        return 0

    token = uuid.uuid4().hex if args.smoke_test else ""
    env = {
        **os.environ,
        "GREALITY_ENV_FILE": str(env_file),
        "GREALITY_WEB_DIR": str(APP / "web"),
        "GREALITY_ASSET_DIR": str(DATA_DIR / "assets"),
        "PYTHONPATH": os.pathsep.join([str(APP / "services"), str(APP / "packages" / "contracts" / "python")]),
        "GREALITY_PORT": str(args.port),
        "GREALITY_BIND_HOST": "127.0.0.1",
        "GREALITY_ALLOWED_ORIGINS": f"http://127.0.0.1:{args.port},http://localhost:{args.port}",
        "GREALITY_OPEN_BROWSER": "0" if (args.no_browser or args.smoke_test) else "1",
        "PYTHONUNBUFFERED": "1",
    }
    if not has_key:
        env["GREALITY_INTERPRETER"] = "rules"  # the Gemini interpreter refuses to start without a key
    if token:
        env["GREALITY_TOKEN"] = token
    # Idempotent: already-applied versions are skipped, so a fresh database is set up on first launch.
    print("Database schema:", flush=True)
    if subprocess.run([sys.executable, "-m", "world_api.migrate"], cwd=APP, env=env).returncode != 0:
        check("!!", "Database migration failed. Check DATABASE_URL and your network connection.")
        return 1

    server = subprocess.Popen([sys.executable, "-m", "world_api"], cwd=APP, env=env)
    print(f"G-reality started on http://127.0.0.1:{args.port}. Press Ctrl+C to stop.", flush=True)
    try:
        if args.smoke_test:
            smoke(args.port, token, server)
            return 0
        return server.wait()
    except KeyboardInterrupt:
        return 0
    except RuntimeError as exc:
        check("!!", str(exc))
        print("Smoke test FAIL.", flush=True)
        return 1
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(10)
            except subprocess.TimeoutExpired:
                server.kill()
        print("G-reality stopped.", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
