@echo off
REM prism-ctl - start, stop or restart Stream Manager from anywhere local.
REM Point a Stream Deck "System > Open" key at this file with an argument:
REM
REM     "...\prism-ctl.bat" restart
REM
REM No token, no network credential: it reads data\runtime.json, which only a
REM process already running as you can open.
setlocal
cd /d "%~dp0"
where python >nul 2>&1
if %errorlevel%==0 (set "PY=python") else (set "PY=py")
%PY% -m stream_manager.ctl %*
exit /b %errorlevel%
