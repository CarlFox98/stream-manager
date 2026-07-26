"""Config file loading/validation, env vars, and derived paths."""
import json, os

# stream_manager/ always lives directly under the app's install root, next to
# stream-manager.py, config.json, .env, and static/.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

CONFIG_DEFAULTS = {
    "port": 5000,
    "poll_interval": 5,
    "twitch_user": "NeoTheFox98",
    "assets_dir": r"%USERPROFILE%\Pictures\OBS Assets",
    "log_file": "server.log",
    "lan": False,
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
}


def _load_config():
    config = dict(CONFIG_DEFAULTS)
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                config.update(json.load(f))
        except Exception as e:
            print(f"[config] Failed to load config.json: {e}")

    for key, typ in [("poll_interval", (int, float)), ("port", int), ("lan", bool),
                     ("interactive_enabled", bool), ("command_prefix", str),
                     ("redeem_poll_interval", (int, float)),
                     ("wheels", dict), ("redeems", dict), ("cooldowns", dict),
                     ("automation", dict), ("eventsub", dict)]:
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


ASSETS_DIR = os.path.normpath(os.path.realpath(os.path.expandvars(config["assets_dir"])))
OVERLAYS_DIR = os.path.join(ASSETS_DIR, "overlays")
TWITCH_USER = config["twitch_user"]

# ── .env ──────────────────────────────────────────────────────────────
_env_path = os.path.join(BASE_DIR, ".env")
if os.path.isfile(_env_path):
    for _line in open(_env_path):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            k, v = _line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

TWITCH_CLIENT_ID = os.environ.get("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "")
