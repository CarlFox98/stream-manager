@echo off
title Build Stream Manager .exe
cd /d "%~dp0"
echo Installing PyInstaller (if needed)...
pip install pyinstaller >nul 2>&1
echo Building StreamManager.exe ...
pyinstaller --clean --noconfirm stream-manager.spec
echo.
echo Done. Your exe is at:  dist\StreamManager.exe
echo Put your config.json and .env next to the exe before running it.
pause
