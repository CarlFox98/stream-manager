"""Shared in-memory app state and the background polling loop."""
import threading, time

from . import __version__
from .config import config
from .lifecycle import supervised
from .obs import get_obs_status
from .system import get_system_stats
from .twitch import get_twitch_status, get_twitch_user_info

state = {
    # `ws`: whether this round's OBS reading came from its WebSocket. Without
    # it, "streaming: False" only means "process detection can't tell".
    "obs": {"running": False, "pid": None, "uptime": 0,
            "streaming": False, "recording": False, "scene": "", "ws": False},
    "twitch": {"live": False, "title": "", "game": "", "viewers": 0, "started_at": "", "uptime": "", "connected": False,
               "display_name": "", "profile_image_url": "", "view_count": 0,
               # when Helix last answered /streams; a failed call leaves `live`
               # as it was, so this is how a caller tells fresh from stale
               "checked_at": 0.0},
    "system": {"cpu": 0, "ram_pct": 0, "ram_used_gb": 0, "ram_total_gb": 0, "gpu": ""},
    # `version` rides along on /api/status, which the dashboard already polls
    # every 2s — the chip used to be hardcoded in dashboard.html and sat two
    # releases stale, which is exactly the thing you want to glance at after
    # an update.
    # `supervised` is whether anything will relaunch us on exit code 42.
    # Without it the dashboard would offer a Restart button that is really
    # a Shutdown button with a hopeful label.
    "server": {"started_at": time.time(), "uptime": "", "port": 5000,
               "version": __version__, "supervised": supervised(),
               "polled_at": 0.0},
    "scenes": {"active_set": None, "available": []},
    "requests": [],
    # Same log minus "Served: <file>" lines. Kept separately because static
    # files arrive in bursts and push real events out of the 100-line buffer.
    "events": []
}


def compute_uptime(ts):
    if not ts: return ""
    delta = int(time.time() - ts)
    if delta < 60: return f"{delta}s"
    h, r = divmod(delta, 3600); m, s = divmod(r, 60)
    return f"{h}h {m}m" if h else f"{m}m {s}s"


def poll_loop():
    while True:
        get_obs_status(state)
        get_system_stats(state)
        get_twitch_status(state)
        get_twitch_user_info(state)
        state["server"]["uptime"] = compute_uptime(state["server"]["started_at"])
        # Refreshed rather than frozen at import so the dashboard's Restart
        # button and the endpoint behind it can never disagree about whether
        # a supervisor exists.
        state["server"]["supervised"] = supervised()
        state["server"]["polled_at"] = time.time()
        time.sleep(config["poll_interval"])


threading.Thread(target=poll_loop, daemon=True).start()
