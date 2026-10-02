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
# `_badges()` runs on the IRC receive thread — the same thread that answers
# Twitch's PING and dispatches every !command. So the Helix fetch NEVER happens
# inline: callers get the current (possibly empty) map at once and a single
# background thread refreshes it. The first messages of a session may render
# without badge images; the alternative was up to 12s of blocked socket reads
# per message, which costs the ping timeout and drops the connection.
_badge_lock = threading.Lock()
_badge_cache = {"map": {}, "at": 0.0, "ok": False}
_badge_refreshing = False
_BADGE_TTL = 3600.0     # a good map stays good for an hour
# Applies while we have never had a map. Once one fetch has succeeded, `ok`
# stays true and a later failure backs off the full TTL — deliberate: the map
# we already hold is still correct, so there is nothing to hurry for.
_BADGE_RETRY = 60.0


def _fetch_badge_sets(url, token, client_id):
    import json, urllib.request
    req = urllib.request.Request(url, headers={
        "Client-ID": client_id, "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=6) as r:
        return json.loads(r.read()).get("data", []) or []


def _build_badge_map():
    """Fetch and assemble {'<set>/<version>': {...}}. Raises on any failure."""
    from . import twitch, twitch_auth
    from .config import TWITCH_CLIENT_ID
    token = twitch.get_access_token()
    if not token or not TWITCH_CLIENT_ID:
        raise RuntimeError("no token")
    urls = ["https://api.twitch.tv/helix/chat/badges/global"]
    bid = (twitch_auth.auth or {}).get("user_id") or ""
    if bid:
        urls.append(f"https://api.twitch.tv/helix/chat/badges?broadcaster_id={bid}")
    built = {}
    for u in urls:
        for s in _fetch_badge_sets(u, token, TWITCH_CLIENT_ID):
            sid = s.get("set_id", "")
            for v in s.get("versions", []) or []:
                built[f"{sid}/{v.get('id','')}"] = {
                    "url": v.get("image_url_2x") or v.get("image_url_1x") or "",
                    "title": v.get("title", "") or sid,
                }
    return built


def _refresh_badges():
    """Body of the refresh. Never raises; ALWAYS clears the in-flight flag —
    a thread that died without clearing it would freeze badges for the session."""
    global _badge_refreshing
    try:
        built = _build_badge_map()
    except Exception as e:
        with _badge_lock:
            # Stamp `at` so _BADGE_RETRY applies. Staleness is judged on `at`
            # alone; gating it on `ok` is what made every single message retry.
            _badge_cache["at"] = time.time()
            _badge_refreshing = False
        print(f"[chatfeed] badge fetch failed ({e}); rendering without badges")
        return
    except BaseException:
        with _badge_lock:
            _badge_cache["at"] = time.time()
            _badge_refreshing = False
        raise
    with _badge_lock:
        # REPLACE the map, never mutate it in place: _badges() iterates the
        # object it was handed without holding the lock.
        _badge_cache.update({"map": built, "at": time.time(), "ok": True})
        _badge_refreshing = False


def _badge_map(force=False):
    """The cached badge map. Never raises and never blocks the caller."""
    global _badge_refreshing
    now = time.time()
    with _badge_lock:
        ttl = _BADGE_TTL if _badge_cache["ok"] else _BADGE_RETRY
        current = _badge_cache["map"]
        if not (force or (now - _badge_cache["at"]) >= ttl):
            return current
        if _badge_refreshing:
            return current                # one refresh in flight is enough
        _badge_refreshing = True
    try:
        threading.Thread(target=_refresh_badges, name="prism-badges",
                         daemon=True).start()
    except RuntimeError:                   # out of threads: don't latch the flag
        with _badge_lock:
            _badge_refreshing = False
    return current


def warm():
    """Kick an off-thread badge fetch. Called once after the IRC JOIN so the
    map is usually ready before the first message needs it."""
    _badge_map()


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


# A /me line arrives CTCP-wrapped, and Twitch measures emote offsets against
# the WRAPPED string — so the prefix length must be subtracted, not ignored.
_ACTION_PREFIX = "\x01ACTION "


def split_action(text):
    """('the body', shift). shift is the code points stripped off the front."""
    if (text.startswith(_ACTION_PREFIX) and text.endswith("\x01")
            and len(text) > len(_ACTION_PREFIX)):
        return text[len(_ACTION_PREFIX):-1], len(_ACTION_PREFIX)
    return text, 0


def _spans_fit(text, spans, offset, strict=True):
    """True if every span, shifted by `offset`, lands on a plausible emote name.

    `strict` also demands the span be delimited by whitespace or a string edge.
    Twitch matches emote codes as whole space-separated words, so a real span
    always is — and without that demand a shifted span happily lands INSIDE the
    preceding word: 'absolutely Kappa' with a body-relative 11-15 shifted by 8
    gives 'olute', which has no whitespace and was accepted, rendering the emote
    image over the middle of 'absolutely'.
    """
    n = len(text)
    for start, end, _ in spans:
        start -= offset
        end -= offset
        if start < 0 or end < start or end >= n:
            return False
        name = text[start:end + 1]
        if not name or any(c.isspace() or ord(c) < 32 for c in name):
            return False
        if strict and not (start == 0 or text[start - 1].isspace()):
            return False
        if strict and not (end + 1 == n or text[end + 1].isspace()):
            return False
    return True


def _best_offset(text, emotes_tag, shift):
    """Which origin a /me message's emote offsets are measured from.

    Twitch does not document whether a /me line's emote positions count against
    the CTCP-wrapped string or the unwrapped body, and picking wrong renders
    every emote in the message over the wrong characters. So don't pick: try
    both and keep the one whose spans land on whole words. `shift` (the wrapped
    reading, which is what other IRC clients compensate for) is tried first and
    so wins a tie.

    The loose pass exists so this can never do WORSE than trusting `shift`
    outright: if neither origin produces word-delimited spans, the strictness is
    abandoned rather than allowed to flip a correct reading.
    """
    if not shift:
        return 0
    spans = _parse_emotes(emotes_tag)
    if not spans:
        return shift
    for strict in (True, False):
        if _spans_fit(text, spans, shift, strict):
            return shift
        if _spans_fit(text, spans, 0, strict):
            return 0
    return shift


def fragments(text, emotes_tag, self_login="", offset=0):
    """Ordered render list. Twitch emotes are placed from their authoritative
    offsets first; only the remaining text is scanned for mentions.

    `offset` is subtracted from every span, for a /me body that has had its
    CTCP wrapper removed. Spans that do not land wholly inside `text` are
    dropped rather than clamped: a clamped span silently eats the rest of the
    line, because the cursor then sits past the end.
    """
    spans = _parse_emotes(emotes_tag)
    out, cursor, n = [], 0, len(text)
    for start, end, eid in spans:
        start -= offset
        end -= offset
        if start < cursor or end < start or start >= n or end >= n:
            continue                      # overlapping or out of range: skip
        if start > cursor:
            out.extend(_split_mentions(text[cursor:start], self_login))
        out.append({"type": "emote", "id": eid, "name": text[start:end + 1],
                    "url": EMOTE_CDN.format(id=eid)})
        cursor = end + 1
    if cursor < n:
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
    body, shift = split_action(text or "")
    emo = tags.get("emotes", "")
    frags = apply_third_party(fragments(body, emo, me, offset=_best_offset(body, emo, shift)))
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
        # Additive field, no CONTRACT_VERSION bump: an overlay that predates it
        # just renders the line upright instead of refusing the whole payload,
        # which is the safer of the two failure modes when only one of the pair
        # has been redeployed.
        "action": bool(shift),
        "reply_to": _reply(tags),
        "text": body,
        "fragments": frags,
        "mentions": [f["login"] for f in frags if f.get("type") == "mention"],
    }


# ------------------------------------------------------------ moderation ----
# Until these existed, a message a mod deleted stayed on the overlay until it
# scrolled off — and with ?ageout=0 (the default) that meant the rest of the
# stream. Both arrive on the twitch.tv/commands + twitch.tv/tags capabilities
# chat.py already requests, so nothing new is negotiated for them.
def clearmsg(tags, text=""):
    """CLEARMSG — a single message deleted. `target-msg-id` is the id the
    overlay tagged the node with, so removal needs no server-side buffer."""
    return {"v": CONTRACT_VERSION, "kind": "clearmsg",
            "ts": int(time.time() * 1000),
            "target_id": tags.get("target-msg-id", "") or "",
            "login": (tags.get("login", "") or "").lower(),
            "text": text or ""}


def clearchat(tags, target_login=""):
    """CLEARCHAT — a timeout or ban (one user), or a full clear.

    Twitch sends `target-user-id` plus a trailing login for one user and omits
    both when the whole room is cleared, so empty user_id AND login means
    "remove everything".
    """
    dur = tags.get("ban-duration", "") or ""
    try:
        seconds = int(dur)
    except (ValueError, TypeError):
        seconds = 0
    return {"v": CONTRACT_VERSION, "kind": "clearchat",
            "ts": int(time.time() * 1000),
            "user_id": tags.get("target-user-id", "") or "",
            "login": (target_login or "").lower(),
            "seconds": seconds}


# ---------------------------------------------------------- event cards ----
# Phase 5: subs, resubs, gifts, raids and cheers render inline in chat as
# highlighted cards. Additive: they ride the same v1 contract (`kind:"event"`
# is ignored by an overlay that predates it, and a cheer is still a `msg` that
# merely gains an `event` field), so no CONTRACT_VERSION bump.
_TIERS = {"1000": "Tier 1", "2000": "Tier 2", "3000": "Tier 3", "Prime": "Prime"}


def _int(v, dflt=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return dflt


def _event_label(mid, tags):
    tier = _TIERS.get(tags.get("msg-param-sub-plan", ""), "")
    months = _int(tags.get("msg-param-cumulative-months"))
    if mid == "sub":
        return "sub", f"subscribed{' with ' + tier if tier else ''}!"
    if mid == "resub":
        return "resub", f"resubscribed — {months} months{' · ' + tier if tier else ''}!"
    if mid == "subgift":
        to = unescape_tag(tags.get("msg-param-recipient-display-name") or "")
        return "gift", f"gifted a {tier or 'Tier 1'} sub to {to}!"
    if mid == "submysterygift":
        n = _int(tags.get("msg-param-mass-gift-count"), 1)
        return "giftbomb", f"is gifting {n} sub{'s' if n != 1 else ''} to the community!"
    if mid == "raid":
        n = _int(tags.get("msg-param-viewerCount"))
        return "raid", f"is raiding with {n} viewer{'s' if n != 1 else ''}!"
    if mid == "announcement":
        return "announcement", "announcement"
    return None, None


def usernotice(tags, text=""):
    """One USERNOTICE -> an `event` card, or None for kinds we don't show.

    Individual gifts that belong to a mass gift are dropped: the
    submysterygift line already says "gifting 20 subs", and twenty more cards
    would flush the whole chat off screen.
    """
    mid = tags.get("msg-id", "") or ""
    if mid == "subgift" and tags.get("msg-param-community-gift-id"):
        return None
    etype, label = _event_label(mid, tags)
    if not etype:
        return None
    login = (tags.get("login") or "").lower()
    uid = tags.get("user-id", "") or ""
    me = _self_login()
    body = text or ""
    frags = apply_third_party(fragments(body, tags.get("emotes", ""), me)) if body else []
    name = unescape_tag(tags.get("display-name") or "") or login
    if etype == "raid":
        name = unescape_tag(tags.get("msg-param-displayName") or "") or name
    return {
        "v": CONTRACT_VERSION,
        "kind": "event",
        "id": tags.get("id", "") or "",
        "ts": _int(tags.get("tmi-sent-ts")) or int(time.time() * 1000),
        "user": {"login": login, "name": name, "id": uid, "color": _color(tags, uid)},
        "badges": _badges(tags),
        "flags": {"mod": False, "sub": True, "vip": False, "broadcaster": False,
                  "first": False, "returning": False},
        "bits": 0,
        "action": False,
        "reply_to": None,
        "event": {"type": etype, "label": label,
                  "system": unescape_tag(tags.get("system-msg") or "")},
        "text": body,
        "fragments": frags,
        "mentions": [f["login"] for f in frags if f.get("type") == "mention"],
    }


def cheer_event(msg):
    """Give a cheer `msg` its card line in place (it stays kind "msg")."""
    if msg.get("bits", 0) > 0:
        b = msg["bits"]
        msg["event"] = {"type": "cheer", "label": f"cheered {b} bit{'s' if b != 1 else ''}!", "system": ""}
    return msg


# ---------------------------------------------------- third-party emotes ----
# Phase 6: 7TV, BetterTTV and FrankerFaceZ emotes. Same rule as badges: the
# fetch never runs on the IRC thread. The map is replaced whole, never mutated.
_tp_lock = threading.Lock()
_tp_cache = {"map": {}, "at": 0.0, "ok": False}
_tp_refreshing = False
_TP_TTL = 1800.0
_TP_RETRY = 120.0


def _get_json(url, timeout=6):
    import json, urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "PRISM-StreamManager"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _seventv(data):
    out = {}
    for e in ((data or {}).get("emote_set") or data or {}).get("emotes", []) or []:
        name, eid = e.get("name"), e.get("id")
        if name and eid:
            out[name] = {"url": f"https://cdn.7tv.app/emote/{eid}/2x.webp", "provider": "7tv", "id": eid}
    return out


def _bttv(items):
    return {e["code"]: {"url": f"https://cdn.betterttv.net/emote/{e['id']}/2x", "provider": "bttv", "id": e["id"]}
            for e in items or [] if e.get("code") and e.get("id")}


def _ffz(data):
    out = {}
    for s in ((data or {}).get("sets") or {}).values():
        for e in s.get("emoticons", []) or []:
            urls = e.get("urls") or {}
            u = urls.get("2") or urls.get("1") or ""
            if e.get("name") and u:
                out[e["name"]] = {"url": u if u.startswith("http") else "https:" + u,
                                  "provider": "ffz", "id": str(e.get("id", ""))}
    return out


def _build_tp_map(channel_id):
    """Globals first, channel sets last so a channel emote wins a name clash;
    within a tier the order is FFZ < BTTV < 7TV. Each provider fails alone."""
    layers = []
    sources = [
        ("ffz-global", "https://api.frankerfacez.com/v1/set/global", _ffz),
        ("bttv-global", "https://api.betterttv.net/3/cached/emotes/global", _bttv),
        ("7tv-global", "https://7tv.io/v3/emote-sets/global", _seventv),
    ]
    if channel_id:
        sources += [
            ("ffz", f"https://api.frankerfacez.com/v1/room/id/{channel_id}", _ffz),
            ("bttv", f"https://api.betterttv.net/3/cached/users/twitch/{channel_id}",
             lambda d: _bttv((d or {}).get("channelEmotes", []) + (d or {}).get("sharedEmotes", []))),
            ("7tv", f"https://7tv.io/v3/users/twitch/{channel_id}", _seventv),
        ]
    ok_any = False
    for label, url, parse in sources:
        try:
            layers.append(parse(_get_json(url)))
            ok_any = True
        except Exception as e:
            print(f"[chatfeed] {label} emotes unavailable ({e})")
    merged = {}
    for layer in layers:
        merged.update(layer)
    if not ok_any:
        raise RuntimeError("no emote provider reachable")
    return merged


def _refresh_tp():
    global _tp_refreshing
    try:
        from . import twitch_auth
        built = _build_tp_map((twitch_auth.auth or {}).get("user_id") or "")
    except Exception as e:
        with _tp_lock:
            _tp_cache["at"] = time.time()
            _tp_refreshing = False
        print(f"[chatfeed] third-party emotes unavailable ({e})")
        return
    except BaseException:
        with _tp_lock:
            _tp_cache["at"] = time.time()
            _tp_refreshing = False
        raise
    with _tp_lock:
        _tp_cache.update({"map": built, "at": time.time(), "ok": True})
        _tp_refreshing = False


def third_party_map(force=False):
    """Cached {name: {url, provider, id}}. Never raises, never blocks."""
    global _tp_refreshing
    try:
        from .config import config
        if not (config.get("chat") or {}).get("third_party_emotes", True):
            return {}
    except Exception:
        pass
    now = time.time()
    with _tp_lock:
        ttl = _TP_TTL if _tp_cache["ok"] else _TP_RETRY
        current = _tp_cache["map"]
        if not (force or (now - _tp_cache["at"]) >= ttl) or _tp_refreshing:
            return current
        _tp_refreshing = True
    try:
        threading.Thread(target=_refresh_tp, name="prism-3p-emotes", daemon=True).start()
    except RuntimeError:
        with _tp_lock:
            _tp_refreshing = False
    return current


_WORD_RE = re.compile(r"\S+")


def apply_third_party(frags, emap=None):
    """Split text fragments on whole words that are third-party emote names.
    Twitch-native emotes and mentions are already placed and are left alone."""
    emap = third_party_map() if emap is None else emap
    if not emap:
        return frags
    out = []
    for f in frags:
        if f.get("type") != "text":
            out.append(f)
            continue
        text, last = f["text"], 0
        for m in _WORD_RE.finditer(text):
            hit = emap.get(m.group(0))
            if not hit:
                continue
            if m.start() > last:
                out.append({"type": "text", "text": text[last:m.start()]})
            out.append({"type": "emote", "id": hit["id"], "name": m.group(0),
                        "url": hit["url"], "provider": hit["provider"]})
            last = m.end()
        if last < len(text):
            out.append({"type": "text", "text": text[last:]})
    return out
