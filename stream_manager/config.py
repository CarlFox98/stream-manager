"""Config file loading/validation, env vars, and derived paths."""
import json, os, sys, threading

# Path roots. Running from source, both point at the app root (next to
# stream-manager.py). Frozen with PyInstaller, user data (config.json, .env,
# data/, logs, tokens) lives next to the .exe (BASE_DIR) while bundled read-only
# assets like static/ are extracted to sys._MEIPASS (RESOURCE_DIR).
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
    RESOURCE_DIR = getattr(sys, "_MEIPASS", BASE_DIR)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    RESOURCE_DIR = BASE_DIR
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

CONFIG_DEFAULTS = {
    "port": 5000,
    "poll_interval": 5,
    "twitch_user": "NeoTheFox98",
    "assets_dir": r"%USERPROFILE%\Pictures\OBS Assets",
    "log_file": "server.log",
    "lan": False,
    # Switchable overlay sets — folder names under <assets_dir>/overlays/.
    # Add a set here to make it selectable from the dashboard.
    "scene_sets": ["modern", "retro"],
    # ── interactive layer (chat commands + channel-point redeems) ──
    "interactive_enabled": True,
    "command_prefix": "!",
    "redeem_poll_interval": 3,   # seconds between channel-point redemption polls
    # Wheel content — leave {} to use the built-in defaults (see games.py).
    "wheels": {},
    # Channel-point redeem definitions — leave {} to use the built-in defaults
    # (see redeems.py). Keys: lucky, risky, coinflip, 5050, plus auto_fulfill.
    "redeems": {},
    # Chat-command cooldowns (seconds). Leave {} for built-in defaults
    # (see games.py). Redeems are limited by Twitch itself, not this.
    "cooldowns": {},
    # Automated wheel outcomes (opt-in). When enabled, wheel segments with an
    # "action" field actually change Twitch state. See actions.py + INTERACTIVE.md.
    "automation": {},
    # EventSub (near-instant redemptions + raid/bits/sub hype). Needs the
    # optional websocket-client dep; falls back to polling if unavailable.
    "eventsub": {},
    # Chat-command registry: per-built-in {"enabled": bool} + a "custom" map of
    # user-defined commands. Managed from the dashboard (see commands.py).
    "commands": {},
    # Timed chat messages (auto-posted on an interval). Managed from dashboard.
    "timers": {},
    # Spotify now-playing integration (client id/secret + token cache).
    "spotify": {},
    # Viewer alerts: first-time chatters + new followers (see alerts.py).
    "alerts": {},
}


def _load_config():
    config = dict(CONFIG_DEFAULTS)
    if os.path.isfile(CONFIG_FILE):
        try:
            # Always UTF-8: config.json contains emoji (wheel labels), which the
            # Windows-default cp1252 codec can't decode.
            with open(CONFIG_FILE, encoding="utf-8") as f:
                config.update(json.load(f))
        except Exception as e:
            print(f"[config] Failed to load config.json: {e}")

    for key, typ in [("poll_interval", (int, float)), ("port", int), ("lan", bool),
                     ("interactive_enabled", bool), ("command_prefix", str),
                     ("redeem_poll_interval", (int, float)),
                     ("wheels", dict), ("redeems", dict), ("cooldowns", dict),
                     ("automation", dict), ("eventsub", dict),
                     ("commands", dict), ("timers", dict), ("spotify", dict),
                     ("alerts", dict)]:
        if not isinstance(config.get(key), typ):
            print(f"[config] {key} must be {typ}, got {type(config.get(key)).__name__}, using default {CONFIG_DEFAULTS[key]}")
            config[key] = CONFIG_DEFAULTS[key]
    for key, val in list(config.items()):
        if key not in CONFIG_DEFAULTS:
            print(f"[config] Unknown key '{key}' in config.json, ignoring")
            del config[key]
    for key in CONFIG_DEFAULTS:
        if key not in config:
            print(f"[config] Missing key '{key}' in config.json, using default: {CONFIG_DEFAULTS[key]}")
            config[key] = CONFIG_DEFAULTS[key]
    return config


config = _load_config()


def reload():
    """Re-read config.json in place so wheels/cooldowns/redeems/prefix can be
    changed without restarting. Mutates the shared `config` dict so every module
    that did `from .config import config` sees the update. Path-derived values
    (assets_dir, ports) are intentionally NOT hot-reloaded — those need a restart.
    """
    fresh = _load_config()
    hot_keys = ("interactive_enabled", "command_prefix", "redeem_poll_interval",
                "wheels", "redeems", "cooldowns", "poll_interval")
    for k in hot_keys:
        if k in fresh:
            config[k] = fresh[k]
    return {k: config.get(k) for k in hot_keys}


# ── writing config back to disk ───────────────────────────────────────────
_write_lock = threading.Lock()


def _deep_merge(base, updates):
    """Recursively merge `updates` into `base` (dicts merge, others replace)."""
    for k, v in updates.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _write_config_file():
    """Atomically write the in-memory config to config.json (UTF-8)."""
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({k: config[k] for k in CONFIG_DEFAULTS}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_FILE)


def save_config(updates):
    """Deep-merge `updates` into the live config and persist to config.json.

    Updates the shared `config` dict in place so every module sees the change
    immediately, then writes the whole config back to disk. Returns the merged
    config. Thread-safe.
    """
    with _write_lock:
        _deep_merge(config, updates)
        _write_config_file()
    return config


def delete_config_key(path):
    """Delete a nested key by path list (e.g. ['commands','custom','socials'])."""
    with _write_lock:
        node = config
        for key in path[:-1]:
            if not isinstance(node.get(key), dict):
                return False
            node = node[key]
        if path[-1] in node:
            del node[path[-1]]
            _write_config_file()
            return True
    return False


ASSETS_DIR = os.path.normpath(os.path.realpath(os.path.expandvars(config["assets_dir"])))
OVERLAYS_DIR = os.path.join(ASSETS_DIR, "overlays")
TWITCH_USER = config["twitch_user"]

# ── .env ──────────────────────────────────────────────────────────────
_env_path = os.path.join(BASE_DIR, ".env")
_env_example = os.path.join(BASE_DIR, ".env.example")
# First-run convenience: seed a .env from the template so newcomers have a file
# to edit instead of hunting for the example.
if not os.path.isfile(_env_path) and os.path.isfile(_env_example):
    try:
        import shutil
        shutil.copyfile(_env_example, _env_path)
        print("[config] Created .env from .env.example — add your Twitch credentials, then restart.")
    except Exception as e:
        print(f"[config] Could not create .env automatically: {e}")
if os.path.isfile(_env_path):
    for _line in open(_env_path, encoding="utf-8"):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            k, v = _line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

TWITCH_CLIENT_ID = os.environ.get("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "")

# Spotify now-playing (optional). Public app needs only the id (PKCE);
# a Confidential app can also set the secret.
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")

# Optional password for LAN mode (--lan). When set, remote (non-localhost)
# devices must supply it via HTTP Basic auth to view the dashboard/APIs.
# Localhost (OBS on the same PC) is always exempt.
DASHBOARD_PASSWORD = os.environ.get("SM_DASHBOARD_PASSWORD", "")
