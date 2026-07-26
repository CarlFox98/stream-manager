"""A tiny in-memory event bus that overlay browser-sources poll.

Overlays can't hold a socket open cheaply in OBS, so instead of pushing we let
each overlay long-poll a JSON endpoint. Every effect gets a monotonically
increasing id; an overlay remembers the last id it saw and asks for anything
newer. On first load it calls with no `since`, gets the current head id, and
therefore never replays effects that happened before it opened.

Channels are just string labels ("coinflip", "wheel"); an overlay subscribes to
one. Events are kept in a small ring buffer so a reconnecting overlay can catch
up by a few events but memory stays bounded.
"""
import threading, time
from collections import deque

_lock = threading.Lock()
_seq = 0
_MAX = 100
# channel -> deque[{"id", "channel", "data", "ts"}]
_events = {}
# channel -> list of recent human-readable results (for the dashboard feed)
_history = {}
_HISTORY_MAX = 25


def emit(channel, data, summary=None):
    """Queue an effect for `channel`. Returns the new event id."""
    global _seq
    with _lock:
        _seq += 1
        ev = {"id": _seq, "channel": channel, "data": data, "ts": time.time()}
        _events.setdefault(channel, deque(maxlen=_MAX)).append(ev)
        if summary:
            hist = _history.setdefault(channel, [])
            hist.insert(0, {"id": _seq, "text": summary, "ts": ev["ts"]})
            del hist[_HISTORY_MAX:]
        return _seq


def head(channel):
    """Current latest id for a channel (0 if none) — for an overlay's first poll."""
    with _lock:
        q = _events.get(channel)
        return q[-1]["id"] if q else _seq


def since(channel, after_id):
    """Return {'events': [...newer than after_id...], 'last_id': N}."""
    with _lock:
        q = _events.get(channel)
        if not q:
            return {"events": [], "last_id": _seq}
        evs = [e for e in q if e["id"] > after_id]
        last = q[-1]["id"]
        return {"events": evs, "last_id": last}


def history(channel=None, limit=_HISTORY_MAX):
    """Recent human-readable results, newest first (dashboard feed)."""
    with _lock:
        if channel is not None:
            return list(_history.get(channel, []))[:limit]
        merged = []
        for ch, items in _history.items():
            for it in items:
                merged.append({**it, "channel": ch})
        merged.sort(key=lambda x: x["ts"], reverse=True)
        return merged[:limit]
