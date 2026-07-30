# Moon Scanner durable launcher - single instance, auto-restart, free-safe defaults.
# Usage:  powershell -ExecutionPolicy Bypass -File .\run-server.ps1
# Or double-click start.bat

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

$Host.UI.RawUI.WindowTitle = "Moon Scanner - keep this window open"
$port = 8765
$url = "http://127.0.0.1:$port"
$lockFile = Join-Path $PSScriptRoot "data\server.lock"
$logDir = Join-Path $PSScriptRoot "data"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Test-ServerUp {
  try {
    $r = Invoke-WebRequest "$url/api/health" -UseBasicParsing -TimeoutSec 3
    return $r.StatusCode -eq 200
  } catch {
    return $false
  }
}

function Stop-PortListeners {
  param([int]$Port)
  # IMPORTANT: never use $pid - PowerShell automatic variable is THIS process
  try {
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
      $procId = $c.OwningProcess
      if ($procId -and $procId -ne 0 -and $procId -ne $PID) {
        Write-Host "  Stopping old PID $procId on port $Port"
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
      }
    }
  } catch {
    # fallback netstat
    netstat -ano | Select-String ":$Port" | Select-String "LISTENING" | ForEach-Object {
      $procId = ($_.ToString() -split '\s+')[-1]
      if ($procId -match '^\d+$' -and [int]$procId -ne $PID) {
        Write-Host "  taskkill PID $procId"
        & taskkill /F /PID $procId 2>$null
      }
    }
  }
}

# Already healthy? Don't start a second copy.
if (Test-ServerUp) {
  Write-Host "Already running at $url"
  Write-Host "Opening browser..."
  Start-Process $url
  Write-Host "Press Enter to stop monitoring (server keeps running)..."
  # Just monitor
  while ($true) {
    Start-Sleep 10
    if (-not (Test-ServerUp)) {
      Write-Host "$(Get-Date -Format o) Server went down - will restart..."
      break
    }
  }
}

Write-Host "============================================================"
Write-Host " Moon Scanner"
Write-Host " $url"
Write-Host " KEEP THIS WINDOW OPEN or the site will go down."
Write-Host "============================================================"
Write-Host ""

# Free-safe defaults only when no paid RPC is configured (respect .env via child process).
# Read .env for HELIUS / SOLANA_RPC without forcing WS off if user has a key.
$envPath = Join-Path $PSScriptRoot ".env"
$hasPaidRpc = $false
if (Test-Path $envPath) {
  Get-Content $envPath -ErrorAction SilentlyContinue | ForEach-Object {
    $line = $_.Trim()
    if ($line -match '^\s*#' -or $line -eq "") { return }
    if ($line -match '^(HELIUS_API_KEY|SOLANA_RPC_HTTP|SOLANA_RPC_WSS|SOLANA_RPC_URL)\s*=\s*(.+)$') {
      $val = $Matches[2].Trim().Trim('"').Trim("'")
      if ($val.Length -gt 4) { $hasPaidRpc = $true }
    }
  }
}
if ($env:HELIUS_API_KEY -or $env:SOLANA_RPC_HTTP -or $env:SOLANA_RPC_WSS) {
  $hasPaidRpc = $true
}

if (-not $env:DISABLE_SOLANA_WS) {
  if ($hasPaidRpc) {
    # Leave unset so config.py auto-enables WS with paid endpoint
    Write-Host "Mode: paid/custom RPC detected - Solana WS left to config auto"
  } else {
    $env:DISABLE_SOLANA_WS = "1"
    Write-Host "Mode: free-safe (Solana WS off; set HELIUS_API_KEY or DISABLE_SOLANA_WS=0)"
  }
}
if (-not $env:REALTIME_PUMP_POLL_SEC) {
  $env:REALTIME_PUMP_POLL_SEC = $(if ($hasPaidRpc) { "2" } else { "5" })
}
if (-not $env:LEARNING_ACTIVE_CAP_PUBLIC) { $env:LEARNING_ACTIVE_CAP_PUBLIC = "20" }
if (-not $env:SOLANA_WS_MODE) { $env:SOLANA_WS_MODE = "logs" }
Write-Host ""

$restart = 0
while ($true) {
  Stop-PortListeners -Port $port
  Start-Sleep -Seconds 1

  $restart++
  $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  Write-Host "[$stamp] Starting uvicorn (attempt $restart)..."

  $outLog = Join-Path $logDir "uvicorn.out.log"
  $errLog = Join-Path $logDir "uvicorn.err.log"

  $p = Start-Process -FilePath "py" -ArgumentList @(
    "-3", "-m", "uvicorn", "main:app",
    "--host", "127.0.0.1",
    "--port", "$port",
    "--log-level", "info"
  ) -WorkingDirectory $PSScriptRoot -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog

  if (-not $p) {
    Write-Host "Failed to start python/uvicorn. Is Python installed?"
    Start-Sleep 5
    continue
  }

  "$($p.Id)|$(Get-Date -Format o)" | Set-Content -Path $lockFile -Encoding ascii
  Write-Host "  PID $($p.Id) - waiting for health..."

  $up = $false
  for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    if ($p.HasExited) {
      Write-Host "  Process exited early code=$($p.ExitCode)"
      break
    }
    if (Test-ServerUp) {
      $up = $true
      break
    }
  }

  if ($up) {
    Write-Host "  UP $url  (health 200)"
    if ($restart -eq 1) {
      try { Start-Process $url } catch {}
    }
    # Watch until process dies
    while (-not $p.HasExited) {
      Start-Sleep -Seconds 5
      if (-not (Test-ServerUp)) {
        # process might still be hanging - kill and restart
        Write-Host "$(Get-Date -Format o) Health failed - restarting..."
        try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
        break
      }
    }
    if ($p.HasExited) {
      Write-Host "$(Get-Date -Format o) uvicorn exited code=$($p.ExitCode) - restart in 3s"
    }
  } else {
    Write-Host "  Did not become healthy - restart in 3s"
    if (-not $p.HasExited) {
      try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
    }
    if (Test-Path $errLog) {
      Write-Host "  Last errors:"
      Get-Content $errLog -Tail 15 -ErrorAction SilentlyContinue
    }
  }

  Start-Sleep -Seconds 3
}
