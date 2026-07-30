@echo off
cd /d "%~dp0"
title Moon Scanner (free)
echo Free / public-RPC mode — same durable launcher as start.bat
set DISABLE_SOLANA_WS=1
set REALTIME_PUMP_POLL_SEC=5
set LEARNING_ACTIVE_CAP_PUBLIC=20
set SOLANA_WS_MODE=logs
set HELIUS_API_KEY=
set SOLANA_RPC_HTTP=
set SOLANA_RPC_WSS=
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-server.ps1"
