@echo off
REM mcp-server - lets Claude read Stream Manager's status over MCP.
REM
REM Don't run this by hand: the Claude desktop app launches it and talks to it
REM over stdin/stdout. Add it to Claude's config as a local MCP server, e.g.
REM
REM     "stream-manager": { "command": "C:\\...\\mcp-server.bat" }
REM
REM Needs Python 3.10+ and:  python -m pip install -r requirements-mcp.txt
REM
REM Nothing may be echoed to stdout here: stdout is the MCP channel, and one
REM stray line breaks the connection. Errors from Python go to stderr.
setlocal
cd /d "%~dp0"
where python >nul 2>&1
if %errorlevel%==0 (set "PY=python") else (set "PY=py")
%PY% -m stream_manager.mcp_server %*
exit /b %errorlevel%
