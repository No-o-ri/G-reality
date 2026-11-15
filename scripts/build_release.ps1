[CmdletBinding()]
param([switch]$SkipWebBuild, [string]$OutDir = 'release')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
try {
    if (-not [IO.Path]::IsPathRooted($OutDir)) { $OutDir = Join-Path $root $OutDir }
    $OutDir = [IO.Path]::GetFullPath($OutDir)
    $stage = Join-Path $OutDir 'G-reality-win64'
    if ($stage -eq $root -or -not $stage.StartsWith($OutDir.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe staging path.' }
    if (-not $SkipWebBuild) {
        Push-Location (Join-Path $root 'apps\web')
        try { & npm.cmd run build; if ($LASTEXITCODE -ne 0) { throw 'Web build failed.' } } finally { Pop-Location }
    }
    if (-not (Test-Path (Join-Path $root 'apps\web\dist\index.html'))) { throw 'Missing apps/web/dist/index.html. Build the web frontend first.' }
    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    function Copy-Tree([string]$Source, [string]$Destination) {
        New-Item -ItemType Directory -Path $Destination -Force | Out-Null
        foreach ($item in Get-ChildItem -LiteralPath $Source -Force) {
            if ($item.Name -eq '__pycache__' -or $item.Name -like '*.pyc') { continue }
            $target = Join-Path $Destination $item.Name
            if ($item.PSIsContainer) { Copy-Tree $item.FullName $target } else { Copy-Item -LiteralPath $item.FullName -Destination $target }
        }
    }
    foreach ($name in @('G-reality.cmd','README.txt','env.example')) { Copy-Item -LiteralPath (Join-Path $root "packaging\$name") -Destination $stage }
    Copy-Tree (Join-Path $root 'packaging\launcher') (Join-Path $stage 'launcher')
    New-Item -ItemType Directory -Path (Join-Path $stage 'app') -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $root 'packaging\requirements-runtime.txt') -Destination (Join-Path $stage 'app\requirements.txt')
    foreach ($path in @('services\world_api','services\gpu','packages\contracts\python','packages\contracts\schemas','database\migrations')) { Copy-Tree (Join-Path $root $path) (Join-Path $stage "app\$path") }
    New-Item -ItemType Directory -Path (Join-Path $stage 'app\scripts') -Force | Out-Null
    foreach ($name in @('wsl-gpu.sh','chunk3_reconstruct.py')) {
        $source = Join-Path $root "scripts\$name"
        if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination (Join-Path $stage 'app\scripts') }
    }
    Copy-Tree (Join-Path $root 'apps\web\dist') (Join-Path $stage 'app\web')
    Push-Location $root
    try {
        $commit = & git rev-parse --short HEAD
        if ($LASTEXITCODE -ne 0) { throw 'Cannot determine git revision.' }
        $status = & git status --porcelain
        if ($LASTEXITCODE -ne 0) { throw 'Cannot determine git status.' }
    } finally { Pop-Location }
    $version = Join-Path $stage 'VERSION.txt'
    $files = @(Get-ChildItem -LiteralPath $stage -Recurse -File -Force)
    $bytes = ($files | Measure-Object Length -Sum).Sum
    $count = $files.Count + 1
    $prefix = "Commit: $commit`r`nDirty: $([bool]$status)`r`nBuilt UTC: $([DateTime]::UtcNow.ToString('o'))`r`nFile count: $count`r`nTotal bytes: "
    $total = [long]$bytes
    do { $previous = $total; $body = $prefix + $total + "`r`n"; $total = [long]$bytes + [Text.Encoding]::UTF8.GetByteCount($body) } while ($total -ne $previous)
    [IO.File]::WriteAllText($version, $body, (New-Object Text.UTF8Encoding $false))
    foreach ($file in Get-ChildItem -LiteralPath $stage -Recurse -File -Force) {
        if ($file.Name -in @('.env.local','.env')) { throw "Secret scan failed: forbidden file $($file.Name)" }
        $content = [IO.File]::ReadAllText($file.FullName)
        if ($content -cmatch 'DATABASE_URL=postgres|npg_|AIza[0-9A-Za-z_\-]{20,}') { throw "Secret scan failed in $($file.FullName). Contents suppressed." }
    }
    # Also fail if any non-trivial value from the developer's own secrets files appears anywhere in the release.
    $values = @()
    foreach ($envPath in @((Join-Path $root '.env.local'), (Join-Path $root '.env'))) {
        if (-not (Test-Path -LiteralPath $envPath)) { continue }
        foreach ($line in [IO.File]::ReadAllLines($envPath)) {
            if ($line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*[''"]?(.*?)[''"]?\s*$') { continue }
            $key = $Matches[1]; $value = $Matches[2]  # copy before the next -match overwrites $Matches
            if ($key -match 'URL|KEY|TOKEN|SECRET|PASSWORD' -and $value.Length -ge 12 -and $value -notmatch '^https?://(127\.0\.0\.1|localhost)') { $values += $value }
        }
    }
    foreach ($file in Get-ChildItem -LiteralPath $stage -Recurse -File -Force) {
        $content = [IO.File]::ReadAllText($file.FullName)
        foreach ($value in $values) { if ($content.Contains($value)) { throw "Secret scan failed: a value from .env.local appears in $($file.FullName). Contents suppressed." } }
    }
    Write-Host "Secret scan PASS ($($values.Count) local secret values checked)."
    $zip = Join-Path $OutDir 'G-reality-win64.zip'
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip
    Write-Host "Release: $zip ($((Get-Item -LiteralPath $zip).Length) bytes)"
    Write-Host "Staged $count files, $total bytes; commit $commit; dirty=$([bool]$status)."
    exit 0
} catch { Write-Host "Build FAILED: $($_.Exception.Message)"; exit 1 }
