@echo off
title Stream Manager
cd /d "%~dp0"
echo Starting Stream Manager...
echo (Leave this window open while you stream. Close it to stop.)
echo.
where python >nul 2>&1
if %errorlevel%==0 (
  python stream-manager.py
) else (
  py stream-manager.py
)
echo.
echo Stream Manager stopped.
pause
