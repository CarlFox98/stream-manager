"""A persistent, numbered quote book.

Quotes live in data/quotes.json (created on demand). Each quote keeps a stable
`id` so "!quote 12" always refers to the same line even after deletions. The
public functions here are pure data operations; chat wiring lives in games.py.
"""
import json, os, random, threading
from datetime import datetime, timezone

from .config import BASE_DIR

DATA_DIR = os.path.join(BASE_DIR, "data")
QUOTES_FILE = os.path.join(DATA_DIR, "quotes.json")

_lock = threading.Lock()
_store = {"next_id": 1, "quotes": []}
_loaded = False


def _load():
    global _store, _loaded
    if _loaded:
        return
    if os.path.isfile(QUOTES_FILE):
        try:
            with open(QUOTES_FILE, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and "quotes" in d:
                _store = {"next_id": d.get("next_id", 1), "quotes": d["quotes"]}
        except Exception as e:
            print(f"[quotes] Could not read quotes.json: {e}")
    _loaded = True


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = QUOTES_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_store, f, ensure_ascii=False, indent=2)
        os.replace(tmp, QUOTES_FILE)
    except Exception as e:
        print(f"[quotes] Could not write quotes.json: {e}")


def add(text, added_by="", game=""):
    """Add a quote. Returns the created quote dict."""
    text = (text or "").strip()
    if not text:
        return None
    with _lock:
        _load()
        q = {
            "id": _store["next_id"],
            "text": text,
            "added_by": added_by,
            "game": game,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }
        _store["quotes"].append(q)
        _store["next_id"] += 1
        _save()
        return q


def delete(qid):
    """Delete by id. Returns True if something was removed."""
    with _lock:
        _load()
        before = len(_store["quotes"])
        _store["quotes"] = [q for q in _store["quotes"] if q["id"] != qid]
        removed = len(_store["quotes"]) < before
        if removed:
            _save()
        return removed


def get(qid):
    with _lock:
        _load()
        for q in _store["quotes"]:
            if q["id"] == qid:
                return dict(q)
    return None


def random_quote():
    with _lock:
        _load()
        return dict(random.choice(_store["quotes"])) if _store["quotes"] else None


def count():
    with _lock:
        _load()
        return len(_store["quotes"])


def all_quotes():
    with _lock:
        _load()
        return [dict(q) for q in _store["quotes"]]


def format_quote(q):
    """Render a quote for chat, e.g.  #12: "gg" — NeoTheFox98 [Halo, 2026-07-25]."""
    if not q:
        return ""
    meta = []
    if q.get("game"):
        meta.append(q["game"])
    if q.get("date"):
        meta.append(q["date"])
    tail = f" [{', '.join(meta)}]" if meta else ""
    who = f" — {q['added_by']}" if q.get("added_by") else ""
    return f'#{q["id"]}: "{q["text"]}"{who}{tail}'
