# Start the local G-reality companion (world API). Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File scripts\start-companion.ps1
# Reads secrets from the git-ignored .env.local in the repository root. Binds to 127.0.0.1 by default
# and prints a pairing token that clients must send as "Authorization: Bearer <token>".

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "Virtual environment missing. Create it with:" -ForegroundColor Yellow
    Write-Host "  python -m venv .venv"
    Write-Host "  .venv\Scripts\python -m pip install -r requirements.txt"
    exit 1
}
if (-not (Test-Path (Join-Path $root ".env.local"))) {
    Write-Host ".env.local not found. Copy .env.example to .env.local and fill in DATABASE_URL." -ForegroundColor Yellow
    exit 1
}

$env:PYTHONPATH = "$root\services;$root\packages\contracts\python"
Set-Location $root
& $python -m world_api
