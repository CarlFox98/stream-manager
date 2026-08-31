# PyInstaller build spec — produces a single Stream Manager .exe.
# Build on Windows:  pip install pyinstaller  &&  pyinstaller --clean --noconfirm stream-manager.spec
# Output: dist/StreamManager.exe  (put config.json / .env next to it; data/ + logs
# are created alongside the exe on first run).
# static/ is bundled inside the exe and read from the extraction dir at runtime
# (see RESOURCE_DIR in stream_manager/config.py).

block_cipher = None

_hidden = [f"stream_manager.{m}" for m in (
    "actions", "alerts", "chat", "cli", "commands", "config", "console",
    "cooldowns", "effects", "eventsub", "games", "logging_util", "obs",
    "obs_ws", "quotes", "redeems", "scenes", "server", "spotify", "state",
    "stats", "system", "timers", "twitch", "twitch_auth", "updater",
)] + ["psutil", "websocket"]

a = Analysis(
    ["stream-manager.py"],
    pathex=[],
    binaries=[],
    datas=[("static", "static")],
    hiddenimports=_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="StreamManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=True,          # keep the console window (it's the status log)
    disable_windowed_traceback=False,
    icon=None,             # set to a .ico path if you have one
)
