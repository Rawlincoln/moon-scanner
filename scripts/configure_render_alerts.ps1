# Configure Telegram 24/7 alerts on Render for moon-scanner-9tlz.
# Usage:
#   $env:RENDER_API_KEY = "rnd_..."
#   powershell -ExecutionPolicy Bypass -File .\scripts\configure_render_alerts.ps1
#
# Or put RENDER_API_KEY=... in moon-scanner\.env (never commit).

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Read-DotEnv {
  $map = @{}
  if (-not (Test-Path ".env")) { return $map }
  Get-Content ".env" | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#") -or $line -notmatch "=") { return }
    $k, $v = $line.Split("=", 2)
    $map[$k.Trim()] = $v.Trim().Trim('"').Trim("'")
  }
  return $map
}

$envMap = Read-DotEnv
$apiKey = $env:RENDER_API_KEY
if (-not $apiKey) { $apiKey = $envMap["RENDER_API_KEY"] }
if (-not $apiKey) {
  Write-Host "Missing RENDER_API_KEY." -ForegroundColor Yellow
  Write-Host "1) Open https://dashboard.render.com/u/settings#api-keys"
  Write-Host "2) Create API Key, copy it"
  Write-Host "3) Run:  `$env:RENDER_API_KEY='rnd_...'; .\scripts\configure_render_alerts.ps1"
  Write-Host "   Or add RENDER_API_KEY=rnd_... to .env and re-run this script."
  exit 2
}

$token = $envMap["TELEGRAM_BOT_TOKEN"]
$chat = $envMap["TELEGRAM_CHAT_ID"]
if (-not $token -or -not $chat) {
  Write-Host "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing in .env" -ForegroundColor Red
  exit 2
}

$cronSecret = $envMap["TELEGRAM_CRON_SECRET"]
if (-not $cronSecret) {
  $bytes = New-Object byte[] 24
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $cronSecret = ([Convert]::ToBase64String($bytes) -replace "[^a-zA-Z0-9]", "").Substring(0, 28)
  Add-Content ".env" "`nTELEGRAM_CRON_SECRET=$cronSecret"
  Write-Host "Generated TELEGRAM_CRON_SECRET in .env"
}

$headers = @{
  Authorization = "Bearer $apiKey"
  Accept        = "application/json"
  "Content-Type" = "application/json"
}

Write-Host "Listing Render services..."
$servicesRaw = Invoke-RestMethod -Uri "https://api.render.com/v1/services?limit=50" -Headers $headers
$services = @()
foreach ($row in $servicesRaw) {
  if ($row.service) { $services += $row.service } else { $services += $row }
}

$svc = $services | Where-Object {
  $_.name -match "moon-scanner" -or
  ($_.serviceDetails.urlencoded -match "moon-scanner-9tlz") -or
  ($_.serviceDetails.url -match "moon-scanner-9tlz")
} | Select-Object -First 1

if (-not $svc) {
  Write-Host "Could not find moon-scanner service. Found:" -ForegroundColor Yellow
  $services | ForEach-Object { Write-Host (" - " + $_.name + " " + $_.id) }
  exit 3
}

$serviceId = $svc.id
Write-Host "Using service: $($svc.name) ($serviceId)"

# GET existing env vars (paginated)
$existing = @{}
$cursor = $null
do {
  $uri = "https://api.render.com/v1/services/$serviceId/env-vars?limit=100"
  if ($cursor) { $uri += "&cursor=$cursor" }
  $page = Invoke-RestMethod -Uri $uri -Headers $headers
  $cursor = $null
  foreach ($item in $page) {
    $ev = if ($item.envVar) { $item.envVar } else { $item }
    if ($ev.key) { $existing[$ev.key] = $ev.value }
    if ($item.cursor) { $cursor = $item.cursor }
  }
} while ($false)

# Merge telegram + free-safe defaults (do not wipe HELIUS etc.)
$existing["TELEGRAM_BOT_TOKEN"] = $token
$existing["TELEGRAM_CHAT_ID"] = $chat
$existing["TELEGRAM_ALERTS"] = "1"
$existing["TELEGRAM_ALERT_FEEDS"] = $(if ($envMap["TELEGRAM_ALERT_FEEDS"]) { $envMap["TELEGRAM_ALERT_FEEDS"] } else { "moon,snipe,heat" })
$existing["TELEGRAM_ALERT_INTERVAL_SEC"] = $(if ($envMap["TELEGRAM_ALERT_INTERVAL_SEC"]) { $envMap["TELEGRAM_ALERT_INTERVAL_SEC"] } else { "45" })
$existing["TELEGRAM_CRON_SECRET"] = $cronSecret
$existing["MOON_SCANNER_DEPLOY"] = "render"
if (-not $existing.ContainsKey("DISABLE_SOLANA_WS")) {
  $existing["DISABLE_SOLANA_WS"] = "1"
}
if ($envMap["ADMIN_API_KEY"] -and -not $existing.ContainsKey("ADMIN_API_KEY")) {
  $existing["ADMIN_API_KEY"] = $envMap["ADMIN_API_KEY"]
}
if ($envMap["HELIUS_API_KEY"] -and -not $existing.ContainsKey("HELIUS_API_KEY")) {
  $existing["HELIUS_API_KEY"] = $envMap["HELIUS_API_KEY"]
}

$body = @()
foreach ($k in $existing.Keys) {
  $body += @{ key = $k; value = [string]$existing[$k] }
}

Write-Host "Updating env vars (merge, not wipe of unknown keys)..."
# Render PUT replaces entire list - we merged existing+new above
$json = $body | ConvertTo-Json -Depth 5
# ConvertTo-Json may produce single object if one item
if ($body.Count -eq 1) { $json = "[$json]" }

Invoke-RestMethod -Method PUT -Uri "https://api.render.com/v1/services/$serviceId/env-vars" `
  -Headers $headers -Body $json | Out-Null

Write-Host "Triggering deploy..."
try {
  Invoke-RestMethod -Method POST -Uri "https://api.render.com/v1/services/$serviceId/deploys" `
    -Headers $headers -Body "{}" | Out-Null
} catch {
  # some accounts want empty body differently
  try {
    Invoke-RestMethod -Method POST -Uri "https://api.render.com/v1/services/$serviceId/deploys" `
      -Headers $headers -Body '{"clearCache":"do_not_clear"}' | Out-Null
  } catch {
    Write-Host "Deploy trigger warning: $($_.Exception.Message)" -ForegroundColor Yellow
  }
}

Write-Host ""
Write-Host "Render Telegram env configured + deploy started." -ForegroundColor Green
Write-Host "Service: https://moon-scanner-9tlz.onrender.com"
Write-Host "Status:  https://moon-scanner-9tlz.onrender.com/api/alerts/status"
Write-Host ""
Write-Host "FREE tier sleep fix - create cron every 2-3 min at cron-job.org:" -ForegroundColor Cyan
Write-Host "https://moon-scanner-9tlz.onrender.com/api/alerts/telegram/tick?key=$cronSecret"
Write-Host ""
Write-Host "(Optional always-on: upgrade service plan to Starter in Render dashboard)"

