"""Owed rewards: wheel outcomes the streamer has to deliver by hand.

"Name the next run", "Bad pun", the poll result to act on, a prediction to
resolve. Before this list they were announced once in chat and forgotten.
Now each becomes a card on the dashboard until it's marked done or skipped.
Persisted (temp-then-rename) so a restart mid-stream loses nothing.
"""
import json, os, threading, time, uuid

from .config import BASE_DIR

_FILE = os.path.join(BASE_DIR, "data", "owed.json")
_KEEP_RESOLVED = 50
_lock = threading.Lock()
_items = []
_loaded = False


def _load():
    global _loaded, _items
    if _loaded:
        return
    try:
        with open(_FILE, encoding="utf-8") as f:
            d = json.load(f)
        _items = [x for x in d if isinstance(x, dict) and "id" in x] if isinstance(d, list) else []
    except FileNotFoundError:
        _items = []
    except Exception as e:
        print(f"[owed] owed.json unreadable ({e}) — starting empty")
        _items = []
    _loaded = True


def _save():
    try:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_items, f, indent=1)
        os.replace(tmp, _FILE)
    except Exception as e:
        print(f"[owed] could not save: {e}")


def add(text, user="", source="", detail=""):
    with _lock:
        _load()
        item = {"id": uuid.uuid4().hex[:10], "text": text, "user": user, "source": source,
                "detail": detail, "created": time.time(), "status": "open"}
        _items.append(item)
        _save()
        return dict(item)


def update(item_id, **fields):
    with _lock:
        _load()
        for it in _items:
            if it["id"] == item_id:
                it.update({k: v for k, v in fields.items() if k in ("text", "detail")})
                _save()
                return dict(it)
    return None


def resolve(item_id, status="done"):
    if status not in ("done", "skipped"):
        return None
    with _lock:
        _load()
        for it in _items:
            if it["id"] == item_id and it["status"] == "open":
                it["status"] = status
                it["resolved"] = time.time()
                _trim()
                _save()
                return dict(it)
    return None


def _trim():
    resolved = [i for i in _items if i["status"] != "open"]
    if len(resolved) > _KEEP_RESOLVED:
        drop = {i["id"] for i in sorted(resolved, key=lambda i: i.get("resolved", 0))[:-_KEEP_RESOLVED]}
        _items[:] = [i for i in _items if i["id"] not in drop]


def items(include_resolved=False):
    with _lock:
        _load()
        out = [dict(i) for i in _items if include_resolved or i["status"] == "open"]
    out.sort(key=lambda i: i["created"])
    return out
