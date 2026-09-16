@echo off
title Stream Manager v0.10.6 - publish and repair the v0.10.5 tag
cd /d "%~dp0"
echo Starting publish...> "%~dp0PUSH-RESULT.txt"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish_v0106.ps1" >> "%~dp0PUSH-RESULT.txt" 2>&1
echo. >> "%~dp0PUSH-RESULT.txt"
echo powershell exit code: %ERRORLEVEL% >> "%~dp0PUSH-RESULT.txt"
type "%~dp0PUSH-RESULT.txt"
echo.
pause
