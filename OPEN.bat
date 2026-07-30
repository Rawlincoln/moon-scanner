@echo off
cd /d "%~dp0"
title Moon Scanner
echo Starting Moon Scanner and opening browser...
echo Keep the PowerShell window open!
echo.

REM If already up, just open browser
powershell -NoProfile -Command "try { $r=Invoke-WebRequest http://127.0.0.1:8765/api/health -UseBasicParsing -TimeoutSec 2; if($r.StatusCode -eq 200){ Start-Process http://127.0.0.1:8765; exit 0 } } catch { exit 1 }"
if %ERRORLEVEL%==0 (
  echo Already running — browser opened.
  pause
  exit /b 0
)

start "Moon Scanner Server" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-server.ps1"
timeout /t 8 /nobreak >nul
start http://127.0.0.1:8765
echo.
echo If the page is blank, wait 5 more seconds and refresh.
echo Do NOT close the "Moon Scanner Server" window.
pause
