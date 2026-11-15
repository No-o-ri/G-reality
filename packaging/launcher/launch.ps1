[CmdletBinding()]
param(
    [ValidateRange(1,65535)][int]$Port = 8765,
    [string]$EnvFile,
    [switch]$NoBrowser,
    [switch]$SmokeTest,
    [switch]$PreflightOnly
)
$ErrorActionPreference = 'Stop'
$releaseRoot = Split-Path $PSScriptRoot -Parent
$app = Join-Path $releaseRoot 'app'
$child = $null
$jobHandle = [IntPtr]::Zero
$transcribing = $false
$exitCode = 1
$savedEnvironment = @{}
$clock = [Diagnostics.Stopwatch]::StartNew()
function Check([string]$Marker, [string]$Message) { Write-Host "[$Marker] $Message" }
function Read-Settings([string]$Path) {
    $result = @{}
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        if ($line -match '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $key = $Matches[1]
            $value = $Matches[2]
            if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) { $value = $value.Substring(1, $value.Length - 2) }
            elseif ($value -match '^(.*?)\s+#.*$') { $value = $Matches[1].TrimEnd() }
            $result[$key] = $value
        }
    }
    return $result
}
# Quote native arguments using Windows CommandLineToArgvW conventions.
function Quote-Argument([string]$Value) {
    return '"' + ([regex]::Replace([regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1')) + '"'
}
function Invoke-Wsl([string[]]$Arguments, [int]$TimeoutMs) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = 'wsl.exe'
    $info.Arguments = ($Arguments | ForEach-Object { Quote-Argument $_ }) -join ' '
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    # wsl.exe list output is UTF-16LE, including when redirected.
    $info.StandardOutputEncoding = [Text.Encoding]::Unicode
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutMs)) { $process.Kill(); throw 'WSL check timed out' }
        return @{ Code = $process.ExitCode; Output = $stdout.GetAwaiter().GetResult().Replace([string][char]0, '') }
    } finally { $process.Dispose() }
}
function Read-Secret([string]$Prompt) {
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}
function Set-LaunchEnvironment([string]$Name, [string]$Value) {
    $savedEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process')
    [Environment]::SetEnvironmentVariable($Name, $Value, 'Process')
}
try {
    $localRoot = Join-Path $env:LOCALAPPDATA 'G-reality'
    $logs = Join-Path $localRoot 'logs'
    New-Item -ItemType Directory -Path $logs -Force | Out-Null
    $logBase = Join-Path $logs ('launcher-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
    Start-Transcript -Path ($logBase + '.log') | Out-Null
    $transcribing = $true
    Write-Host 'G-reality preflight'
    # PATH candidates, then the per-user python.org install locations (PATH is not refreshed after a winget install).
    function Find-Python {
        $candidates = @('py','python') + @(Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Programs\Python') -Filter python.exe -Recurse -Depth 1 -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object { $_.FullName })
        foreach ($candidate in $candidates) {
            $command = Get-Command $candidate -ErrorAction SilentlyContinue
            if (-not $command) { continue }
            $prefix = @()
            if ($candidate -eq 'py') { $prefix = @('-3') }
            try {
                $versionText = (& $command.Source @prefix --version 2>&1 | Out-String)
                if ($LASTEXITCODE -eq 0 -and $versionText -match 'Python\s+(\d+)\.(\d+)\.(\d+)') {
                    $version = [version]($Matches[1] + '.' + $Matches[2] + '.' + $Matches[3])
                    if ($version -ge [version]'3.12.0') { return @{ Path = $command.Source; Prefix = $prefix; Version = $version } }
                }
            } catch { }
        }
        return $null
    }
    $found = Find-Python
    if (-not $found -and -not ($SmokeTest -or $PreflightOnly) -and (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        Check '--' 'Python 3.12+ was not found. G-reality can install the official, signed Python 3.12 from python.org with winget (per user, no administrator rights).'
        if ((Read-Host 'Install Python 3.12 now? [Y/n]') -notmatch '^\s*[nN]') {
            & winget.exe install --exact --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
            $found = Find-Python
        }
    }
    if (-not $found) { Check '!!' 'Python 3.12+ required. Install from https://www.python.org/downloads/windows/ and enable the launcher or PATH.'; throw 'Python requirement failed.' }
    $python = $found.Path
    $pythonPrefix = $found.Prefix
    Check 'ok' "Python $($found.Version)"
    if ($EnvFile) {
        if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) { Check '!!' 'The specified secrets file does not exist.'; throw 'Secrets file missing.' }
        $chosenEnv = (Resolve-Path -LiteralPath $EnvFile).Path
    } else {
        $chosenEnv = $null
        foreach ($path in @((Join-Path $releaseRoot '.env.local'), (Join-Path $env:APPDATA 'G-reality\.env.local'))) {
            if (Test-Path -LiteralPath $path -PathType Leaf) { $chosenEnv = $path; break }
        }
    }
    if (-not $chosenEnv -and -not ($SmokeTest -or $PreflightOnly)) {
        # First-run setup: write %APPDATA%\G-reality\.env.local from hidden console input.
        Write-Host ''
        Write-Host 'First-run setup. G-reality keeps worlds in your own PostgreSQL database; a free Neon project works:'
        Write-Host '  https://neon.tech -> New project -> Connect -> copy the connection string (postgresql://...).'
        Write-Host 'Without a database, the read-only sample world still runs on the G-reality website (no install).'
        $database = Read-Secret 'Paste DATABASE_URL (input hidden; Enter to cancel)'
        if ([string]::IsNullOrWhiteSpace($database)) { Check '--' 'Setup cancelled: no database URL entered.'; throw 'A database URL is required to run the companion.' }
        if ($database -notmatch '^postgres(ql)?://') { Check '!!' 'That does not look like a PostgreSQL URL (postgresql://...).'; throw 'Invalid DATABASE_URL.' }
        $gemini = Read-Secret 'Optional: paste a Gemini API key (input hidden; Enter to skip and use the keyword interpreter)'
        $interpreter = 'rules'
        if (-not [string]::IsNullOrWhiteSpace($gemini)) { $interpreter = 'gemini' }
        $configDir = Join-Path $env:APPDATA 'G-reality'
        New-Item -ItemType Directory -Path $configDir -Force | Out-Null
        $chosenEnv = Join-Path $configDir '.env.local'
        $text = "DATABASE_URL=$($database.Trim())`r`nGEMINI_API_KEY=$($gemini.Trim())`r`nGREALITY_INTERPRETER=$interpreter`r`n"
        [IO.File]::WriteAllText($chosenEnv, $text, (New-Object Text.UTF8Encoding $false))
        Check 'ok' "Settings saved to $chosenEnv (edit or delete it to change them)."
    }
    if (-not $chosenEnv) {
        Check '!!' 'Secrets file missing. Copy env.example to .env.local next to G-reality.cmd or to %APPDATA%\G-reality\.env.local; fill DATABASE_URL and GEMINI_API_KEY.'
        throw 'Secrets file required.'
    }
    $settings = Read-Settings $chosenEnv
    if ([string]::IsNullOrWhiteSpace($settings['DATABASE_URL'])) { Check '!!' 'DATABASE_URL is empty. Fill it in your secrets file.'; throw 'DATABASE_URL required.' }
    Check 'ok' 'Secrets file found; DATABASE_URL is present (value hidden).'
    if ([string]::IsNullOrWhiteSpace($settings['GEMINI_API_KEY'])) { Check '--' 'GEMINI_API_KEY empty: offline keyword interpreter will be used (worlds still generate; less faithful to prompts)' }
    else { Check 'ok' 'GEMINI_API_KEY is present (value hidden).' }
    $listener = New-Object Net.Sockets.TcpListener ([Net.IPAddress]::Loopback), $Port
    try { $listener.Server.ExclusiveAddressUse = $true; $listener.Start(); Check 'ok' "Port $Port is free." }
    catch { Check '!!' "Port $Port is unavailable: $($_.Exception.Message)"; throw 'Select another port with -Port or stop the other instance.' }
    finally { $listener.Stop() }
    try {
        if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { throw 'wsl.exe not installed' }
        $distro = $settings['GREALITY_WSL_DISTRO']
        if ([string]::IsNullOrWhiteSpace($distro)) { $distro = 'Ubuntu' }
        $gpuVenv = $settings['GREALITY_GPU_VENV']
        if ([string]::IsNullOrWhiteSpace($gpuVenv)) { $gpuVenv = '/home/sidno/greality-gpu/.venv' }
        $gpuClock = [Diagnostics.Stopwatch]::StartNew()
        $listed = Invoke-Wsl -Arguments @('-l','-q') -TimeoutMs 4500
        if ($listed.Code -ne 0) { throw 'WSL distro listing failed' }
        $distros = @($listed.Output -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        if ($distros -notcontains $distro) { throw 'configured distro not installed' }
        $remaining = [Math]::Max(1, 9500 - [int]$gpuClock.ElapsedMilliseconds)
        $gpu = Invoke-Wsl -Arguments @('-d',$distro,'--','test','-x',($gpuVenv.TrimEnd('/') + '/bin/python')) -TimeoutMs $remaining
        if ($gpu.Code -ne 0) { throw 'GPU virtual environment Python is not executable' }
        Check 'ok' 'Optional GPU far field environment is available.'
    } catch { Check '--' "Optional GPU far field unavailable: $($_.Exception.Message)" }
    if ($PreflightOnly) { $exitCode = 0 }
    else {
        $requirements = Join-Path $app 'requirements.txt'
        $hash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash.Substring(0,8).ToLowerInvariant()
        $venv = Join-Path $localRoot "venv-$hash"
        $venvPython = Join-Path $venv 'Scripts\python.exe'
        $ready = Join-Path $venv '.ready'
        if (-not (Test-Path -LiteralPath $ready) -or -not (Test-Path -LiteralPath $venvPython)) {
            if (Test-Path -LiteralPath $ready) { Remove-Item -LiteralPath $ready -Force }
            & $python @pythonPrefix -m venv $venv
            if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
            & $venvPython -m pip install --only-binary=:all: -r $requirements
            if ($LASTEXITCODE -ne 0) { throw 'Runtime package installation failed.' }
            [IO.File]::WriteAllText($ready, [DateTime]::UtcNow.ToString('o'))
        }
        Set-LaunchEnvironment 'GREALITY_ENV_FILE' $chosenEnv
        # The Gemini interpreter refuses to start without a key; the process variable overrides the secrets file.
        if ([string]::IsNullOrWhiteSpace($settings['GEMINI_API_KEY'])) { Set-LaunchEnvironment 'GREALITY_INTERPRETER' 'rules' }
        Set-LaunchEnvironment 'GREALITY_WEB_DIR' (Join-Path $app 'web')
        Set-LaunchEnvironment 'PYTHONPATH' ((Join-Path $app 'services') + ';' + (Join-Path $app 'packages\contracts\python'))
        Set-LaunchEnvironment 'GREALITY_PORT' ([string]$Port)
        Set-LaunchEnvironment 'GREALITY_BIND_HOST' '127.0.0.1'
        Set-LaunchEnvironment 'GREALITY_ALLOWED_ORIGINS' "http://127.0.0.1:$Port,http://localhost:$Port"
        $openBrowser = '1'
        if ($NoBrowser -or $SmokeTest) { $openBrowser = '0' }
        Set-LaunchEnvironment 'GREALITY_OPEN_BROWSER' $openBrowser
        Set-LaunchEnvironment 'PYTHONUNBUFFERED' '1'
        # Idempotent: already-applied versions are skipped, so a fresh Neon database is set up on first launch.
        Write-Host 'Database schema:'
        Push-Location $app
        try { & $venvPython -m world_api.migrate } finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) { Check '!!' 'Database migration failed. Check DATABASE_URL and your network connection.'; throw 'Database migration failed.' }
        if ($SmokeTest) { $token = [guid]::NewGuid().ToString('N'); Set-LaunchEnvironment 'GREALITY_TOKEN' $token }
        $stdoutPath = $logBase + '-server-out.log'
        $stderrPath = $logBase + '-server-err.log'
        # Windows closes this job handle even on console-window closure, killing its children.
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
public static class GRealityJob {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool SetInformationJobObject(IntPtr job, int kind, IntPtr info, uint size);
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")]
    public static extern bool CloseHandle(IntPtr handle);
    public static IntPtr Create() {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new Win32Exception();
        int size = IntPtr.Size == 8 ? 144 : 112;
        IntPtr info = Marshal.AllocHGlobal(size);
        try {
            Marshal.Copy(new byte[size], 0, info, size);
            Marshal.WriteInt32(info, 16, 0x2000); // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if (!SetInformationJobObject(job, 9, info, (uint)size)) {
                int error = Marshal.GetLastWin32Error(); CloseHandle(job);
                throw new Win32Exception(error);
            }
            return job;
        } finally { Marshal.FreeHGlobal(info); }
    }
}
'@
        $jobHandle = [GRealityJob]::Create()
        $child = Start-Process -FilePath $venvPython -ArgumentList @('-m','world_api') -WorkingDirectory $app -NoNewWindow -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        if (-not [GRealityJob]::AssignProcessToJobObject($jobHandle, $child.Handle)) { throw 'Could not attach server to launcher lifetime.' }
        Write-Host "G-reality started on http://127.0.0.1:$Port. Press Ctrl+C to stop."
        if ($SmokeTest) {
            $healthClock = [Diagnostics.Stopwatch]::StartNew()
            $healthy = $false
            while ($healthClock.Elapsed.TotalSeconds -lt 90) {
                if ($child.HasExited) { throw 'Server exited before becoming healthy. See server logs.' }
                try {
                    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/v1/health" -TimeoutSec 2
                    if ($health.database -eq 'connected') { $healthy = $true; break }
                } catch { }
                Start-Sleep -Milliseconds 500
            }
            if (-not $healthy) { throw 'Database health did not become connected within 90 seconds.' }
            $page = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/" -TimeoutSec 10
            if ($page.StatusCode -ne 200 -or $page.Content -notmatch 'G-reality') { throw 'Frontend check failed.' }
            $worlds = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/v1/worlds" -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 10
            if ($worlds.StatusCode -ne 200) { throw 'Authenticated worlds check failed.' }
            Write-Host ('Smoke test PASS: health {0:N1}s; total {1:N1}s.' -f $healthClock.Elapsed.TotalSeconds, $clock.Elapsed.TotalSeconds)
            $exitCode = 0
        } else {
            while (-not $child.WaitForExit(500)) { }
            $exitCode = $child.ExitCode
            if ($exitCode -ne 0) { throw "Server exited with code $exitCode. See server logs." }
        }
    }
} catch {
    Check '!!' $_.Exception.Message
    if ($SmokeTest) { Write-Host ('Smoke test FAIL: total {0:N1}s.' -f $clock.Elapsed.TotalSeconds) }
    $exitCode = 1
} finally {
    if ($child) {
        if (-not $child.HasExited) {
            # A job object also terminates the child if the console is closed abruptly.
            Stop-Process -Id $child.Id -Force -ErrorAction SilentlyContinue
            [void]$child.WaitForExit(5000)
        }
        $child.Dispose()
        Write-Host 'G-reality stopped.'
    }
    if ($jobHandle -ne [IntPtr]::Zero) { [void][GRealityJob]::CloseHandle($jobHandle) }
    foreach ($name in $savedEnvironment.Keys) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process') }
    if ($transcribing) { Stop-Transcript | Out-Null }
}
exit $exitCode
