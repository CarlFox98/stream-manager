"""Timed chat messages — auto-post reminders/socials on an interval.

Each timer posts its message to chat every `interval` minutes, but only once at
least `min_lines` chat messages have gone by since it last fired (so timers
don't stack up in a quiet chat). Configured in `config.json` → `timers` and
managed from the dashboard.

    "timers": {
      "enabled": true,
      "list": [
        {"name": "socials", "message": "Follow at twitch.tv/...", "interval": 15, "min_lines": 5, "enabled": true}
      ]
    }
"""
import threading, time

from .config import config

_stop = threading.Event()
_state = {}   # timer name -> {"last_ts", "last_lines"}
_CHECK = 10   # seconds between checks


def _cfg():
    t = config.get("timers")
    return t if isinstance(t, dict) else {}


def _items():
    lst = _cfg().get("list")
    return lst if isinstance(lst, list) else []


def _key(item, idx):
    return (item.get("name") or "").strip().lower() or f"#{idx}"


def _loop():
    from . import chat  # late import: chat.say is the delivery channel
    # seed timers "now" so each first fires after its interval, not immediately
    while not _stop.is_set():
        try:
            if _cfg().get("enabled") and chat.status.get("connected"):
                now = time.time()
                lines = int(chat.status.get("received", 0) or 0)
                live = {}
                for idx, item in enumerate(_items()):
                    if not isinstance(item, dict) or not item.get("enabled", True):
                        continue
                    msg = (item.get("message") or "").strip()
                    if not msg:
                        continue
                    key = _key(item, idx)
                    live[key] = True
                    interval = max(float(item.get("interval", 15) or 15), 0.1) * 60
                    min_lines = int(item.get("min_lines", 0) or 0)
                    st = _state.setdefault(key, {"last_ts": now, "last_lines": lines})
                    if now - st["last_ts"] >= interval and lines - st["last_lines"] >= min_lines:
                        try:
                            chat.say(msg)
                        except Exception as e:
                            print(f"[timers] {key}: {e}")
                        st["last_ts"] = now
                        st["last_lines"] = lines
                # forget timers that were removed/renamed
                for gone in [k for k in _state if k not in live]:
                    _state.pop(gone, None)
        except Exception as e:
            print(f"[timers] loop error: {e}")
        _stop.wait(_CHECK)


def start():
    """Launch the timer loop on a daemon thread. Idempotent."""
    if getattr(start, "_thread", None) and start._thread.is_alive():
        return
    _stop.clear()
    start._thread = threading.Thread(target=_loop, daemon=True)
    start._thread.start()


def stop():
    _stop.set()


def public_list():
    """Timers + their config for the dashboard."""
    return {"enabled": bool(_cfg().get("enabled", False)), "list": _items()}
