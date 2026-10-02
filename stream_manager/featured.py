"""Featured viewers — the Lucky Wheel's "Featured Viewer" outcome.

Replaces three hand-placed image sources on Starting Soon that pointed at files
which later went missing. Winners are kept here (last N, persisted) with their
Twitch avatar URL; static/interactive/featured.html renders them, themed by the
active PRISM set like the other redeem overlays.
"""
import json, os, threading, time

from . import effects, helix
from .config import BASE_DIR, config

_FILE = os.path.join(BASE_DIR, "data", "featured.json")
_lock = threading.Lock()


def _max():
    c = config.get("featured") if isinstance(config.get("featured"), dict) else {}
    try:
        return max(1, min(int(c.get("slots", 3)), 6))
    except (TypeError, ValueError):
        return 3


def _load():
    try:
        with open(_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return [x for x in d if isinstance(x, dict)] if isinstance(d, list) else []
    except Exception:
        return []


def _save(rows):
    os.makedirs(os.path.dirname(_FILE), exist_ok=True)
    tmp = _FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=1)
    os.replace(tmp, _FILE)


def lookup(user_id="", login=""):
    """(display_name, login, avatar_url) from Helix, or Nones."""
    params = {"id": user_id} if user_id else {"login": login} if login else None
    if not params:
        return None, None, None
    code, body = helix.request("GET", "users", params=params)
    if code == 200 and body.get("data"):
        u = body["data"][0]
        return u.get("display_name"), u.get("login"), u.get("profile_image_url")
    return None, None, None


def add(user, user_id="", login=""):
    """Feature a viewer. Returns (ok, message)."""
    name, lg, avatar = lookup(user_id, login or (user or "").lower())
    if not (name or user):
        return False, "no viewer to feature"
    row = {"name": name or user, "login": lg or (login or user or "").lower(),
           "avatar": avatar or "", "user_id": user_id, "ts": time.time()}
    with _lock:
        rows = [r for r in _load() if r.get("login") != row["login"]]
        rows.append(row)
        rows = rows[-_max():]
        _save(rows)
    effects.emit("featured", {"viewers": rows, "new": row["login"]},
                 summary=f"★ Featured viewer: {row['name']}")
    return True, "featured"


def current():
    with _lock:
        return _load()[-_max():]


def remove(login):
    with _lock:
        rows = [r for r in _load() if r.get("login") != login]
        _save(rows)
    effects.emit("featured", {"viewers": rows, "new": ""}, summary=None)
    return True
