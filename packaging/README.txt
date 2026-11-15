G-reality for Windows

Requirements: Windows 10/11, Python 3.12 or newer (python.org), internet
for the first-run package install, a Neon database URL, and optionally a Gemini key.

Copy env.example to .env.local next to G-reality.cmd, or to
%APPDATA%\G-reality\.env.local (create the directory first).
Fill DATABASE_URL and, optionally, GEMINI_API_KEY. Keep this file private.
Without a Gemini key, the offline keyword interpreter still generates worlds,
with less faithful interpretation of prompts.

Double-click G-reality.cmd to start. The first launch installs runtime packages
in %LOCALAPPDATA%\G-reality. The browser opens when the server is healthy.
Stop with Ctrl+C or by closing the launcher window.

Controls: WASD move, mouse look, Shift sprint, Space jump, E interact,
Esc release mouse, F3 diagnostics.

Optional GPU far field: install WSL2 and a CUDA PyTorch virtual environment
inside Linux. Set GREALITY_WSL_DISTRO (default Ubuntu) and GREALITY_GPU_VENV
(default /home/sidno/greality-gpu/.venv) in your secrets file. The optional
tools are in app\scripts. GPU availability is never required to launch.

Troubleshooting:
- Port in use: stop the other instance, or run G-reality.cmd -Port 8770.
- Python missing: install Python 3.12+ from https://www.python.org/downloads/windows/
  and enable its launcher or add Python to PATH.
- Smart App Control: the launcher uses only signed Python and pip wheels.
  Do not disable Smart App Control. Use an official signed Python installation.
- Database unavailable: verify your Neon URL and network connection.
- Logs: %LOCALAPPDATA%\G-reality\logs\launcher-<timestamp>.log
- Use G-reality.cmd -PreflightOnly to check requirements without installing.
- Use -EnvFile "C:\path\to\secrets" to select a different secrets file.
