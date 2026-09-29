@echo off
setlocal
title Stream Manager
cd /d "%~dp0"

REM SM_SUPERVISED tells the app that something out here will bring it back if
REM it exits with code 42. The dashboard's Restart button refuses to pretend
REM otherwise, so without this line it correctly reports there is no supervisor.
set "SM_SUPERVISED=1"

where python >nul 2>&1
if %errorlevel%==0 (set "PY=python") else (set "PY=py")

:run
echo Starting Stream Manager...
echo (Leave this window open while you stream. Close it to stop.)
echo.
%PY% stream-manager.py %*

REM Batch errorlevel tests are ">=", so exactly-42 needs both halves.
if errorlevel 42 if not errorlevel 43 (
  echo.
  echo Restarting Stream Manager...
  echo.
  timeout /t 1 /nobreak >nul
  goto run
)

echo.
echo Stream Manager stopped.
pause
