@echo off
REM Runs Stream Manager under a system-tray icon that owns the process, so
REM Restart works from the tray, the dashboard and the Stream Deck alike.
REM Needs: pip install pystray pillow
setlocal
title Stream Manager (tray)
cd /d "%~dp0"
where python >nul 2>&1
if %errorlevel%==0 (set "PY=python") else (set "PY=py")
%PY% -m stream_manager.tray %*
if errorlevel 1 pause
