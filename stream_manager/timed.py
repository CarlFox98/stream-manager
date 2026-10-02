"""Timed effects: things a wheel outcome turns on that must turn themselves off.

An upside-down PNGtuber, emote-only chat, a swapped overlay set, a week of VIP.
Each one is applied now and undone later, and "later" has to survive a crash,
a restart and an impatient streamer. Three rules make that hold:

1. **Capture, persist, then apply.** A kind's capture() reads the state it is
   about to change (the tuber's transform, the chat settings) and that snapshot
   is written to data/active-effects.json BEFORE apply() runs. If the process
   dies between the two, startup still has what it needs to put things back.
2. **Undo restores the snapshot, not an assumed default.** If chat was already
   in slow mode before Slow Mode landed, it stays in slow mode afterwards.
3. **One effect per (kind, target).** A second hit on an active effect extends
   its timer instead of stacking a second copy whose snapshot would be the
   *already-changed* state — that is how a flip-on-a-flip gets stuck upside down.

Short effects are reverted at shutdown and, if a crash left any behind, at
startup. "Long" effects (VIP for 7 days) persist across restarts and are only
reverted when they expire or the streamer revokes them.
"""
import json, os, threading, time, uuid

from .config import BASE_DIR

_FILE = os.path.join(BASE_DIR, "data", "active-effects.json")
_MAX_REVERT_TRIES = 5

_lock = threading.RLock()
_kinds = {}       # kind -> {"capture", "apply", "revert", "long", "label"}
_active = {}      # id -> entry
_stop = threading.Event()
_thread = None
_listeners = []   # callables(entries) — the status overlay feed


def register(kind, apply, revert, capture=None, long=False, label=None):
    """Declare an effect kind.

    capture(target, params) -> snapshot (JSON-able) or raises
    apply(target, params, snapshot) -> (ok: bool, message: str)
    revert(target, params, snapshot) -> bool
    """
    _kinds[kind] = {"capture": capture, "apply": apply, "revert": revert,
                    "long": bool(long), "label": label or kind}


def _save():
    try:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        tmp = _FILE + ".tmp"
        with _lock:
            data = list(_active.values())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, _FILE)
    except Exception as e:
        print(f"[timed] could not save active effects: {e}")


def _load_file():
    try:
        with open(_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"[timed] active-effects.json unreadable ({e}) — starting empty")
        return []


def _find(kind, target):
    for e in _active.values():
        if e["kind"] == kind and e["target"] == target:
            return e
    return None


def start(kind, target, seconds, params=None, label="", user=""):
    """Apply an effect for `seconds`. Returns (ok, message, entry_or_None).

    An already-active (kind, target) is extended, never re-applied.
    """
    k = _kinds.get(kind)
    if not k:
        return False, f"unknown effect '{kind}'", None
    params = params or {}
    now = time.time()
    with _lock:
        existing = _find(kind, target)
        if existing:
            existing["until"] = max(existing["until"], now) + float(seconds)
            existing["extended"] = existing.get("extended", 0) + 1
            entry = dict(existing)
            extended = True
        else:
            extended = False
    if extended:
        _save()
        _notify()
        return True, "extended", entry

    try:
        snapshot = k["capture"](target, params) if k["capture"] else None
    except Exception as e:
        return False, f"could not read current state: {e}", None

    entry = {"id": uuid.uuid4().hex[:12], "kind": kind, "target": target,
             "params": params, "snapshot": snapshot, "label": label or k["label"],
             "user": user, "started": now, "until": now + float(seconds),
             "long": k["long"], "applied": False, "tries": 0}
    with _lock:
        if _find(kind, target):            # lost a race with another start()
            return start(kind, target, seconds, params, label, user)
        _active[entry["id"]] = entry
    _save()                                # persisted BEFORE apply — rule 1
    try:
        ok, msg = k["apply"](target, params, snapshot)
    except Exception as e:
        ok, msg = False, str(e)
    with _lock:
        if not ok:
            _active.pop(entry["id"], None)
        else:
            entry["applied"] = True
    _save()
    _notify()
    return ok, msg, (dict(entry) if ok else None)


def _revert(entry):
    k = _kinds.get(entry["kind"])
    if not k:
        return True                        # nothing we know how to undo
    try:
        return bool(k["revert"](entry["target"], entry.get("params") or {}, entry.get("snapshot")))
    except Exception as e:
        print(f"[timed] revert {entry['kind']}:{entry['target']} failed: {e}")
        return False


def end(effect_id):
    """Revert one effect now (dashboard 'Undo' / 'Revoke'). Returns bool."""
    with _lock:
        entry = _active.get(effect_id)
    if not entry:
        return False
    ok = _revert(entry)
    if ok:
        with _lock:
            _active.pop(effect_id, None)
        _save()
        _notify()
    return ok


def revert_all(include_long=False):
    """Undo every short effect (and long ones too if asked). Returns count."""
    with _lock:
        targets = [e for e in _active.values() if include_long or not e.get("long")]
    n = 0
    for e in targets:
        if end(e["id"]):
            n += 1
    return n


def tick(now=None):
    """Revert everything that has expired. Called by the loop; testable alone."""
    now = now or time.time()
    with _lock:
        due = [e for e in _active.values() if e["until"] <= now]
    for e in due:
        if _revert(e):
            with _lock:
                _active.pop(e["id"], None)
        else:
            with _lock:
                e["tries"] = e.get("tries", 0) + 1
                give_up = e["tries"] >= _MAX_REVERT_TRIES
                if give_up:
                    _active.pop(e["id"], None)
                    print(f"[timed] gave up reverting {e['kind']}:{e['target']} — undo it by hand")
                else:
                    e["until"] = now + 5 * e["tries"]      # back off and retry
    if due:
        _save()
        _notify()


def recover():
    """Startup: undo short effects a crash left applied; keep long ones.

    Entries persisted before apply() ran ("applied": false) are reverted too —
    we cannot know whether apply() got as far as changing anything, and
    reverting to the captured snapshot is harmless either way.
    """
    leftover = _load_file()
    now = time.time()
    keep, undone = [], 0
    for e in leftover:
        if not isinstance(e, dict) or "kind" not in e or "id" not in e:
            continue
        if e.get("long") and e.get("until", 0) > now:
            keep.append(e)
            continue
        if _revert(e):
            undone += 1
        else:
            e["until"] = now + 5            # retry from the loop
            keep.append(e)
    with _lock:
        _active.clear()
        for e in keep:
            _active[e["id"]] = e
    _save()
    return undone


def active(include_long=True):
    now = time.time()
    with _lock:
        out = [dict(e) for e in _active.values() if include_long or not e.get("long")]
    for e in out:
        e["remaining"] = max(0, int(e["until"] - now))
        e.pop("snapshot", None)            # internal; never shipped to pages
    out.sort(key=lambda e: e["until"])
    return out


def on_change(fn):
    _listeners.append(fn)


def _notify():
    for fn in list(_listeners):
        try:
            fn(active(include_long=False))
        except Exception as e:
            print(f"[timed] listener error: {e}")


def _loop():
    while not _stop.wait(0.5):
        try:
            tick()
        except Exception as e:
            print(f"[timed] tick error: {e}")


def start_loop():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()


def stop_loop(revert_short=True):
    """Shutdown: stop ticking, then undo short effects so nothing is left
    flipped/tinted/emote-only after the app is gone."""
    _stop.set()
    if revert_short:
        revert_all(include_long=False)
    _save()
