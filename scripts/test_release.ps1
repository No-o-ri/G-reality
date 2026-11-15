[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$EnvFile)
$ErrorActionPreference = 'Stop'
$temp = Join-Path ([IO.Path]::GetTempPath()) ('G-reality-test-' + [guid]::NewGuid().ToString('N'))
$code = 1
try {
    $secrets = (Resolve-Path -LiteralPath $EnvFile).Path
    $zip = Join-Path (Split-Path $PSScriptRoot -Parent) 'release\G-reality-win64.zip'
    New-Item -ItemType Directory -Path $temp | Out-Null
    Expand-Archive -LiteralPath $zip -DestinationPath $temp
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $temp 'launcher\launch.ps1') -SmokeTest -NoBrowser -Port 8799 -EnvFile $secrets
    $code = $LASTEXITCODE
} catch { Write-Host "Release test error: $($_.Exception.Message)" } finally {
    if ([IO.Path]::GetFullPath($temp).StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase)) { Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue }
}
if ($code -eq 0) { Write-Host 'Release test PASS' } else { Write-Host 'Release test FAIL' }
exit $code
