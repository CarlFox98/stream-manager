"""Lightweight, persistent stats + leaderboard for the interactive games.

Every game outcome is recorded here (in memory, flushed to data/stats.json on a
short debounce). The dashboard reads summary() for leaderboards: most active
players, jackpots, and the most common wheel outcomes.

Kept intentionally small and best-effort: a corrupt/missing file just starts
fresh, and a failed write is logged and ignored.
"""
import json, os, threading, time
from collections import Counter
from datetime import datetime, timezone

from .config import BASE_DIR

STATS_FILE = os.path.join(BASE_DIR, "data", "stats.json")

_lock = threading.Lock()
_last_save = 0.0
_SAVE_DEBOUNCE = 5.0
_dirty = False

_store = {
    "totals": {},        # game -> count
    "users": {},         # user -> {"plays": n, "wins": n}
    "wheel_outcomes": {},  # label -> count
    "jackpots": 0,       # slots/lucky jackpots
    "recent": [],        # last N outcomes
    "commands": {},      # custom command name -> times used
}
_RECENT_MAX = 50
_loaded = False

# Dashboard test buttons play as this user. Their plays stay in "recent" (tagged
# test) so you can see a test ran, but never count toward totals, jackpots or the
# leaderboard. Older test plays already in stats.json are filtered at read time.
TEST_USER = "Dashboard"


def is_test_user(user):
    return (user or "").strip().lower() == TEST_USER.lower()


def _load():
    global _store, _loaded
    if _loaded:
        return
    _loaded = True
    if os.path.isfile(STATS_FILE):
        try:
            with open(STATS_FILE, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                _store.update({k: d.get(k, _store[k]) for k in _store})
        except Exception as e:
            print(f"[stats] could not read stats.json: {e}")


def _save(force=False):
    global _last_save, _dirty
    now = time.time()
    if not force and (now - _last_save) < _SAVE_DEBOUNCE:
        _dirty = True
        return
    try:
        os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
        tmp = STATS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_store, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATS_FILE)
        _last_save = now
        _dirty = False
    except Exception as e:
        print(f"[stats] could not write stats.json: {e}")


def _is_win(game, outcome):
    o = (outcome or "").upper()
    if game in ("5050",):
        return o == "WIN"
    if game in ("slots", "lucky"):
        return "JACKPOT" in o
    return False


def record(game, subtype, user, outcome):
    """Record one game result. Safe to call from any thread."""
    with _lock:
        _load()
        if is_test_user(user):
            _store["recent"].insert(0, {
                "game": game, "user": user, "outcome": outcome, "test": True,
                "ts": datetime.now(timezone.utc).timestamp(),
            })
            del _store["recent"][_RECENT_MAX:]
            _save()
            return
        _store["totals"][game] = _store["totals"].get(game, 0) + 1
        u = _store["users"].setdefault(user or "anon", {"plays": 0, "wins": 0})
        u["plays"] += 1
        # Wheel spins are recorded as game "wheel" with the wheel ("lucky"/"risky")
        # as the subtype; win/outcome tracking keys on the wheel, not "wheel".
        kind = subtype if game == "wheel" else game
        win = _is_win(kind, outcome)
        if win:
            u["wins"] += 1
            _store["jackpots"] += 1
        if kind in ("lucky", "risky"):
            _store["wheel_outcomes"][outcome] = _store["wheel_outcomes"].get(outcome, 0) + 1
        _store["recent"].insert(0, {
            "game": game, "user": user, "outcome": outcome,
            "ts": datetime.now(timezone.utc).timestamp(),
        })
        del _store["recent"][_RECENT_MAX:]
        _save()


def summary():
    with _lock:
        _load()
        real = {u: s for u, s in _store["users"].items() if not is_test_user(u)}
        test_wins = sum(s.get("wins", 0) for u, s in _store["users"].items() if is_test_user(u))
        top_players = sorted(real.items(), key=lambda kv: kv[1]["plays"], reverse=True)[:10]
        top_outcomes = Counter(_store["wheel_outcomes"]).most_common(8)
        return {
            "totals": dict(_store["totals"]),
            "total_plays": sum(_store["totals"].values()),
            "jackpots": max(0, _store["jackpots"] - test_wins),
            "top_players": [{"user": u, "plays": s["plays"], "wins": s["wins"]} for u, s in top_players],
            "top_outcomes": [{"label": l, "count": c} for l, c in top_outcomes],
            "recent": list(_store["recent"])[:15],
        }


def bump_command(name):
    """Count one use of a custom command."""
    with _lock:
        _load()
        c = _store.setdefault("commands", {})
        c[name] = c.get(name, 0) + 1
        _save()


def command_uses():
    with _lock:
        _load()
        return dict(_store.get("commands") or {})


def flush():
    with _lock:
        _save(force=True)
