"""Normalise Twitch IRC chat into the PRISM chat overlay's message contract.

`chat.py` already holds the IRC connection and parses tags; this module turns
one parsed PRIVMSG into the JSON object the overlay renders, and owns the
badge-URL cache. Everything here is pure except `_badge_map()`, which does one
cached Helix read — that keeps the interesting logic unit-testable without a
network or a live Twitch.

Contract version 1. Bump CONTRACT_VERSION whenever a field changes meaning so a
stale overlay can refuse a payload instead of rendering it wrong.

Emote positions in the `emotes` tag are CODE POINT offsets. Python indexes str
by code point, so splitting here is correct by construction; doing it in the
browser would need UTF-16 bookkeeping and would silently misplace every emote
after an astral character (i.e. after most emoji).
"""
import re
import threading
import time

CONTRACT_VERSION = 1

EMOTE_CDN = "https://static-cdn.jtvnw.net/emoticons/v2/{id}/default/dark/2.0"

# Twitch's own tag escaping (IRCv3): these appear in reply bodies and system
# messages. chat.py's parser deliberately leaves them raw.
_TAG_UNESCAPE = ((r"\\s", " "), (r"\\:", ";"), (r"\\r", "\r"), (r"\\n", "\n"), (r"\\\\", "\\"))

_MENTION_RE = re.compile(r"@([A-Za-z0-9_]{1,25})")

# Fallback name colours for chatters who never set one. Twitch's own defaults,
# so an uncoloured chatter still gets a stable, readable identity.
_FALLBACK_COLORS = (
    "#FF0000", "#0000FF", "#00FF00", "#B22222", "#FF7F50", "#9ACD32", "#FF4500",
    "#2E8B57", "#DAA520", "#D2691E", "#5F9EA0", "#1E90FF", "#FF69B4", "#8A2BE2",
)


def unescape_tag(v):
    """Undo IRCv3 tag escaping."""
    if not v or "\\" not in v:
        return v or ""
    out, i = [], 0
    while i < len(v):
        if v[i] == "\\" and i + 1 < len(v):
            nxt = v[i + 1]
            out.append({"s": " ", ":": ";", "r": "\r", "n": "\n", "\\": "\\"}.get(nxt, nxt))
            i += 2
        else:
            out.append(v[i])
            i += 1
    return "".join(out)


# ---------------------------------------------------------------- badges ----
_badge_lock = threading.Lock()
_badge_cache = {"map": {}, "at": 0.0, "ok": False}
_BADGE_TTL = 3600.0


def _fetch_badge_sets(url, token, client_id):
    import json, urllib.request
    req = urllib.request.Request(url, headers={
        "Client-ID": client_id, "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=6) as r:
        return json.loads(r.read()).get("data", []) or []


def _badge_map(force=False):
    """{'<set>/<version>': {'url':…, 'title':…}} — cached, never raises.

    A failed fetch keeps whatever we already had and retries after the TTL; it
    must never block or drop a message, so callers just get fewer badges.
    """
    now = time.time()
    with _badge_lock:
        fresh = _badge_cache["ok"] and (now - _badge_cache["at"]) < _BADGE_TTL
        if fresh and not force:
            return _badge_cache["map"]
    built = {}
    try:
        from . import twitch, twitch_auth
        from .config import TWITCH_CLIENT_ID
        token = twitch.get_access_token()
        if not token or not TWITCH_CLIENT_ID:
            raise RuntimeError("no token")
        urls = ["https://api.twitch.tv/helix/chat/badges/global"]
        bid = (twitch_auth.auth or {}).get("user_id") or ""
        if bid:
            urls.append(f"https://api.twitch.tv/helix/chat/badges?broadcaster_id={bid}")
        for u in urls:
            for s in _fetch_badge_sets(u, token, TWITCH_CLIENT_ID):
                sid = s.get("set_id", "")
                for v in s.get("versions", []) or []:
                    built[f"{sid}/{v.get('id','')}"] = {
                        "url": v.get("image_url_2x") or v.get("image_url_1x") or "",
                        "title": v.get("title", "") or sid,
                    }
    except Exception as e:
        with _badge_lock:
            _badge_cache["at"] = now          # back off; keep the old map
            print(f"[chatfeed] badge fetch failed ({e}); rendering without badges")
            return _badge_cache["map"]
    with _badge_lock:
        _badge_cache.update({"map": built, "at": now, "ok": True})
        return built


def _badges(tags):
    raw = tags.get("badges", "") or ""
    if not raw:
        return []
    bmap = _badge_map()
    out = []
    for token in raw.split(","):
        if not token:
            continue
        set_id, _, version = token.partition("/")
        meta = bmap.get(f"{set_id}/{version}") or {}
        out.append({"set": set_id, "version": version,
                    "url": meta.get("url", ""), "title": meta.get("title", "") or set_id})
    return out


def _has_badge(tags, name):
    return any(b.split("/")[0] == name for b in (tags.get("badges", "") or "").split(","))


# ------------------------------------------------------------- fragments ----
def _parse_emotes(raw):
    """'25:0-4,12-16/1902:6-10' -> [(start, end, id)] sorted by start."""
    spans = []
    for group in (raw or "").split("/"):
        if not group or ":" not in group:
            continue
        eid, _, positions = group.partition(":")
        for pos in positions.split(","):
            a, _, b = pos.partition("-")
            try:
                spans.append((int(a), int(b), eid))
            except (ValueError, TypeError):
                continue
    spans.sort(key=lambda s: s[0])
    return spans


def _split_mentions(text, self_login):
    """Turn a text run into text/mention fragments."""
    out, last = [], 0
    for m in _MENTION_RE.finditer(text):
        if m.start() > last:
            out.append({"type": "text", "text": text[last:m.start()]})
        login = m.group(1).lower()
        out.append({"type": "mention", "text": m.group(0), "login": login,
                    "self": bool(self_login) and login == self_login})
        last = m.end()
    if last < len(text):
        out.append({"type": "text", "text": text[last:]})
    return out


def fragments(text, emotes_tag, self_login=""):
    """Ordered render list. Twitch emotes are placed from their authoritative
    offsets first; only the remaining text is scanned for mentions."""
    spans = _parse_emotes(emotes_tag)
    out, cursor = [], 0
    for start, end, eid in spans:
        if start < cursor or start > len(text):
            continue                      # overlapping or out-of-range: skip
        if start > cursor:
            out.extend(_split_mentions(text[cursor:start], self_login))
        name = text[start:end + 1]
        out.append({"type": "emote", "id": eid, "name": name,
                    "url": EMOTE_CDN.format(id=eid)})
        cursor = end + 1
    if cursor < len(text):
        out.extend(_split_mentions(text[cursor:], self_login))
    return [f for f in out if f.get("type") != "text" or f.get("text")]


# --------------------------------------------------------------- message ----
def _self_login():
    try:
        from . import twitch_auth
        from .config import TWITCH_USER
        return ((twitch_auth.auth or {}).get("login") or TWITCH_USER or "").lower()
    except Exception:
        return ""


def _color(tags, user_id):
    c = (tags.get("color") or "").strip()
    if c:
        return c
    try:
        return _FALLBACK_COLORS[int(user_id) % len(_FALLBACK_COLORS)]
    except (ValueError, TypeError):
        return _FALLBACK_COLORS[0]


def _reply(tags):
    pid = tags.get("reply-parent-msg-id") or ""
    if not pid:
        return None
    return {"id": pid,
            "name": unescape_tag(tags.get("reply-parent-display-name") or ""),
            "text": unescape_tag(tags.get("reply-parent-msg-body") or "")}


def message(tags, prefix, text):
    """One parsed PRIVMSG -> the contract's `msg` object."""
    nick = (prefix or "").split("!", 1)[0]
    login = (nick or "").lower()
    uid = tags.get("user-id", "") or ""
    me = _self_login()
    frags = fragments(text or "", tags.get("emotes", ""), me)
    try:
        bits = int(tags.get("bits") or 0)
    except (ValueError, TypeError):
        bits = 0
    try:
        ts = int(tags.get("tmi-sent-ts") or 0) or int(time.time() * 1000)
    except (ValueError, TypeError):
        ts = int(time.time() * 1000)
    return {
        "v": CONTRACT_VERSION,
        "kind": "msg",
        "id": tags.get("id", "") or "",
        "ts": ts,
        "user": {"login": login,
                 "name": unescape_tag(tags.get("display-name") or "") or nick,
                 "id": uid,
                 "color": _color(tags, uid)},
        "badges": _badges(tags),
        "flags": {
            "mod": tags.get("mod") == "1" or _has_badge(tags, "moderator"),
            "sub": tags.get("subscriber") == "1" or _has_badge(tags, "subscriber"),
            "vip": _has_badge(tags, "vip"),
            "broadcaster": _has_badge(tags, "broadcaster"),
            "first": tags.get("first-msg") == "1",
            "returning": tags.get("returning-chatter") == "1",
        },
        "bits": bits,
        "reply_to": _reply(tags),
        "text": text or "",
        "fragments": frags,
        "mentions": [f["login"] for f in frags if f.get("type") == "mention"],
    }
