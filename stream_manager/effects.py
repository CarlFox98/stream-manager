"""A tiny in-memory event bus that overlay browser-sources consume.

Two delivery styles share one bus:

* **Overlays** hit ``GET /api/effects/<channel>?since=<id>``. The server
  *long-polls* — it holds the request open (via ``wait_events``) until a newer
  event exists or a timeout elapses, so effects arrive within a network round
  trip instead of one poll interval, with far fewer requests.
* **The dashboard** opens one ``GET /api/stream`` Server-Sent Events connection
  and receives every effect (across channels) live via ``wait_global``.

Every effect gets a monotonically increasing id; a consumer remembers the last
id it saw and asks for anything newer. A small ring buffer bounds memory while
letting a reconnecting client catch up by a few events.
"""
import threading, time
from collections import deque

_lock = threading.Lock()
_cond = threading.Condition(_lock)   # notified on every emit
_seq = 0
_MAX = 100
# channel -> deque[{"id", "channel", "data", "ts"}]
_events = {}
# global stream of recent events (all channels) for the dashboard SSE feed
_all = deque(maxlen=200)
# channel -> list of recent human-readable results (for the dashboard feed)
_history = {}
_HISTORY_MAX = 25


def emit(channel, data, summary=None):
    """Queue an effect for `channel`. Returns the new event id."""
    global _seq
    with _cond:
        _seq += 1
        ev = {"id": _seq, "channel": channel, "data": data, "ts": time.time(),
              "summary": summary}
        _events.setdefault(channel, deque(maxlen=_MAX)).append(ev)
        _all.append(ev)
        if summary:
            hist = _history.setdefault(channel, [])
            hist.insert(0, {"id": _seq, "text": summary, "ts": ev["ts"]})
            del hist[_HISTORY_MAX:]
        _cond.notify_all()
        return _seq


def head(channel):
    """Current latest id for a channel (0 if none) — for a consumer's first poll."""
    with _lock:
        q = _events.get(channel)
        return q[-1]["id"] if q else _seq


def since(channel, after_id):
    """Return {'events': [...newer than after_id...], 'last_id': N} immediately."""
    with _lock:
        q = _events.get(channel)
        if not q:
            return {"events": [], "last_id": _seq}
        evs = [e for e in q if e["id"] > after_id]
        return {"events": evs, "last_id": q[-1]["id"]}


def wait_events(channel, after_id, timeout=25.0):
    """Long-poll: block until `channel` has an event newer than after_id, or
    `timeout` seconds pass. Returns the same shape as `since`."""
    deadline = time.time() + timeout
    with _cond:
        while True:
            q = _events.get(channel)
            last = q[-1]["id"] if q else _seq
            if last > after_id:
                evs = [e for e in q if e["id"] > after_id] if q else []
                return {"events": evs, "last_id": last}
            remaining = deadline - time.time()
            if remaining <= 0:
                return {"events": [], "last_id": _seq}
            _cond.wait(remaining)


def wait_global(after_id, timeout=25.0):
    """Long-poll across ALL channels (dashboard SSE). Returns
    {'events': [{id, channel, data, summary, ts}, ...], 'last_id': N}."""
    deadline = time.time() + timeout
    with _cond:
        while True:
            last = _all[-1]["id"] if _all else _seq
            if last > after_id:
                evs = [e for e in _all if e["id"] > after_id]
                return {"events": evs, "last_id": last}
            remaining = deadline - time.time()
            if remaining <= 0:
                return {"events": [], "last_id": _seq}
            _cond.wait(remaining)


# channel -> unix ts of the last poll by an overlay (its heartbeat)
_last_poll = {}


def note_poll(channel):
    """Record that an overlay asked for this channel — its liveness heartbeat."""
    # Written from HTTP threads, read from the health-monitor thread; take the
    # lock so a new channel appearing mid-iteration can't blow up subscribers().
    with _lock:
        _last_poll[channel] = time.time()


def subscribers(max_age=90.0):
    """{channel: seconds_since_last_poll} for overlays seen within `max_age`."""
    now = time.time()
    with _lock:
        snapshot = list(_last_poll.items())
    return {ch: round(now - ts, 1) for ch, ts in snapshot if now - ts <= max_age}


def last_poll(channel):
    """Seconds since an overlay last polled this channel, or None if never."""
    with _lock:
        ts = _last_poll.get(channel)
    return None if ts is None else round(time.time() - ts, 1)


def current_id():
    with _lock:
        return _seq


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
