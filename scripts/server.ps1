<#
.SYNOPSIS
  Start, stop, check or restart the ClaimLens backend (uvicorn) in the background.

.DESCRIPTION
  Runs app.main:app from .venv in a hidden background process. Writes the PID to
  data/server.pid and logs to data/server.log and data/server.err.log (data/ is git-ignored).
  Stop kills the whole process tree, and only touches a process on the port if it is a uvicorn app.main:app process.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -NoProfile -File scripts\server.ps1 start
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'status', 'restart')]
    [string]$Action = 'status',

    [int]$Port = 8000
)

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root '.venv\Scripts\python.exe'
$DataDir = Join-Path $Root 'data'
$PidFile = Join-Path $DataDir 'server.pid'
$LogFile = Join-Path $DataDir 'server.log'
$ErrFile = Join-Path $DataDir 'server.err.log'
# 127.0.0.1, not localhost: uvicorn binds IPv4 and PowerShell may try ::1 first.
$BaseUrl = "http://127.0.0.1:$Port"

function Get-ListenerPid {
    # PID of whatever is listening on the port, or $null.
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) { return [int]$conn.OwningProcess }
    return $null
}

function Test-ServerProcess([int]$ProcId) {
    # True only for a process whose command line is our uvicorn app.
    $p = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcId" -ErrorAction SilentlyContinue
    return [bool]($p -and $p.CommandLine -like '*uvicorn*app.main:app*')
}

function Test-Health {
    try {
        Invoke-RestMethod -Uri "$BaseUrl/api/health" -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Show-Status {
    $listener = Get-ListenerPid
    if ($null -eq $listener) {
        Write-Host "Stopped: nothing is listening on port $Port."
        return
    }
    if (Test-ServerProcess $listener) {
        $health = if (Test-Health) { 'healthy' } else { 'not responding to /api/health yet' }
        Write-Host "Running: PID $listener on $BaseUrl ($health)."
        Write-Host "Docs: $BaseUrl/docs"
    } else {
        Write-Host "Port $Port is used by PID $listener, which is not the ClaimLens server. Not touching it."
    }
}

function Start-Server {
    $listener = Get-ListenerPid
    if ($null -ne $listener) {
        Write-Host "Already running or port in use (PID $listener). Use 'status', 'stop' or 'restart'."
        exit 1
    }
    if (-not (Test-Path $Python)) {
        Write-Host "Virtualenv not found at $Python. Create it first (see README.md)."
        exit 1
    }

    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
    $proc = Start-Process -FilePath $Python `
        -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--port', "$Port") `
        -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $LogFile -RedirectStandardError $ErrFile
    Set-Content -Path $PidFile -Value $proc.Id

    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        if (Test-Health) {
            Write-Host "Started ClaimLens backend on $BaseUrl (PID $($proc.Id))."
            Write-Host "Docs: $BaseUrl/docs"
            Write-Host "Logs: $LogFile"
            return
        }
        if ($proc.HasExited) { break }
    }

    Write-Host "Server did not become healthy. Last error output:"
    if (Test-Path $ErrFile) { Get-Content $ErrFile -Tail 20 }
    Write-Host "Full logs: $ErrFile"
    exit 1
}

function Stop-Server {
    $targets = @()
    if (Test-Path $PidFile) {
        $saved = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($saved -match '^\d+$') { $targets += [int]$saved }
    }
    $listener = Get-ListenerPid
    if ($null -ne $listener) { $targets += $listener }

    $stopped = 0
    foreach ($id in ($targets | Select-Object -Unique)) {
        if ((Get-Process -Id $id -ErrorAction SilentlyContinue) -and (Test-ServerProcess $id)) {
            # /T kills the child process too: the venv python.exe launches the real interpreter as a child.
            & taskkill.exe /PID $id /T /F | Out-Null
            $stopped++
        }
    }
    Remove-Item $PidFile -ErrorAction SilentlyContinue

    Start-Sleep -Milliseconds 500
    $left = Get-ListenerPid
    if ($null -ne $left -and -not (Test-ServerProcess $left)) {
        Write-Host "Port $Port is still used by PID $left, which is not the ClaimLens server. Not touching it."
        exit 1
    }
    if ($stopped -gt 0 -and $null -eq $left) {
        Write-Host "Stopped ClaimLens backend."
    } elseif ($null -eq $left) {
        Write-Host "Nothing to stop. The server was not running."
    } else {
        Write-Host "Could not stop the server on port $Port (PID $left)."
        exit 1
    }
}

switch ($Action) {
    'start'   { Start-Server }
    'stop'    { Stop-Server }
    'status'  { Show-Status }
    'restart' { Stop-Server; Start-Server }
}
