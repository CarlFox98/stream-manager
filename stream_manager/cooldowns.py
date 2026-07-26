"""Per-user and global cooldowns for chat-command games.

Channel-point redeems get their limits enforced by Twitch itself (global
cooldown + max-per-user-per-stream, set on the reward — see redeems.py). This
module covers the *chat command* path (!coinflip, !5050, !lucky, …) so viewers
can't spam the overlay or chat.

Two windows per action: a `global` cooldown (anyone) and a `user` cooldown (that
viewer). `check()` records the hit only when it passes, so a blocked attempt
doesn't extend the window. A short notify-throttle stops "you're on cooldown"
replies from becoming their own spam.
"""
import json, os, threading, time

from .config import BASE_DIR

_lock = threading.Lock()
_last_global = {}   # action -> ts of last successful use
_last_user = {}     # (action, user_lower) -> ts
_last_notify = {}   # (action, user_lower) -> ts of last "on cooldown" reply
_NOTIFY_THROTTLE = 5.0
_FILE = os.path.join(BASE_DIR, "data", "cooldowns.json")


def check(action, user, user_cd=0, global_cd=0):
    """Return (allowed, wait_seconds, scope).

    scope is "global" or "user" when blocked, "" when allowed. Passing the check
    records the hit (starting both windows).
    """
    now = time.time()
    ul = (user or "").lower()
    with _lock:
        if global_cd > 0:
            rem = global_cd - (now - _last_global.get(action, 0))
            if rem > 0:
                return False, rem, "global"
        if user_cd > 0 and ul:
            rem = user_cd - (now - _last_user.get((action, ul), 0))
            if rem > 0:
                return False, rem, "user"
        _last_global[action] = now
        if ul:
            _last_user[(action, ul)] = now
        return True, 0.0, ""


def should_notify(action, user):
    """True at most once per _NOTIFY_THROTTLE seconds per (action, user)."""
    now = time.time()
    ul = (user or "").lower()
    with _lock:
        if now - _last_notify.get((action, ul), 0) < _NOTIFY_THROTTLE:
            return False
        _last_notify[(action, ul)] = now
        return True


def reset():
    """Clear all cooldown state (used by tests)."""
    with _lock:
        _last_global.clear()
        _last_user.clear()
        _last_notify.clear()


def save():
    """Persist cooldown windows so they survive a restart."""
    try:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        with _lock:
            data = {
                "global": dict(_last_global),
                "user": {f"{a}|{u}": ts for (a, u), ts in _last_user.items()},
            }
        with open(_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[cooldowns] save failed: {e}")


def load():
    """Restore cooldown windows saved by a previous run (best effort)."""
    if not os.path.isfile(_FILE):
        return
    try:
        with open(_FILE, encoding="utf-8") as f:
            d = json.load(f)
        with _lock:
            _last_global.update({k: float(v) for k, v in (d.get("global") or {}).items()})
            for key, ts in (d.get("user") or {}).items():
                if "|" in key:
                    a, u = key.split("|", 1)
                    _last_user[(a, u)] = float(ts)
    except Exception as e:
        print(f"[cooldowns] load failed: {e}")
