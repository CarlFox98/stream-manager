"""Lightweight, persistent stats + leaderboard for the interactive games.

Every game outcome is recorded here (in memory, flushed to data/stats.json on a
short debounce). The dashboard reads summary() for leaderboards: most active
players, jackpots, and the most common wheel outcomes.

Kept intentionally small and best-effort — a corrupt/missing file just starts
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
}
_RECENT_MAX = 50
_loaded = False


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
        _store["totals"][game] = _store["totals"].get(game, 0) + 1
        u = _store["users"].setdefault(user or "anon", {"plays": 0, "wins": 0})
        u["plays"] += 1
        win = _is_win(game, outcome)
        if win:
            u["wins"] += 1
            _store["jackpots"] += 1
        if game in ("lucky", "risky"):
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
        top_players = sorted(_store["users"].items(), key=lambda kv: kv[1]["plays"], reverse=True)[:10]
        top_outcomes = Counter(_store["wheel_outcomes"]).most_common(8)
        return {
            "totals": dict(_store["totals"]),
            "total_plays": sum(_store["totals"].values()),
            "jackpots": _store["jackpots"],
            "top_players": [{"user": u, "plays": s["plays"], "wins": s["wins"]} for u, s in top_players],
            "top_outcomes": [{"label": l, "count": c} for l, c in top_outcomes],
            "recent": list(_store["recent"])[:15],
        }


def flush():
    with _lock:
        _save(force=True)
