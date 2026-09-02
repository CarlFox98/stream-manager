"""Shoutouts — ported from the standalone PRISM Shoutout service.

One action funnels everything: `do_shoutout(login, source, viewers)` runs the
safety gates, looks the streamer up on Helix, picks a clip, pushes a card to the
overlay and (optionally) posts in chat.

Ported to Stream Manager's plumbing so it runs in-process instead of as a second
bot: chat comes from `chat.py`, raids from `eventsub.py`, the overlay card goes
out over `effects.emit("shoutout", …)`, and Helix uses the token `twitch.py`
already holds. Standard library only.

Config lives in `config.json` → `shoutout` (see DEFAULTS below); every value is
optional and falls back to the tuned defaults carried over from PRISM.
"""
import datetime, json, os, random, re, threading, time
import urllib.error, urllib.parse, urllib.request
from collections import deque

from . import effects
from .config import BASE_DIR, TWITCH_CLIENT_ID, TWITCH_USER, config

LOG_FILE = os.path.join(BASE_DIR, "data", "shoutout-log.jsonl")

DEFAULTS = {
    "enabled": True,
    "mods_only": True,
    "allow_self": False,
    "chat_send": True,
    # triggers
    "raid_shoutout": True,
    "raid_min_viewers": 2,
    "raid_require_approval": False,
    "raid_approval_ttl": 120,
    "raid_allowlist": [],
    "blocklist": [],
    # guards (seconds)
    "cooldown_sec": 3,
    "repeat_guard_sec": 30,
    "max_queue_sec": 120,
    "lookup_guard_sec": 60,
    # card timing (ms)
    "hold_ms": 18000,
    "noclip_hold_ms": 8000,
    # clips
    "clip_volume": 0.85,
    "clip_fade_in_ms": 320,
    "clip_recent_days": 7,
    "clip_recent_pool": 8,
    "clip_popular_days": 30,
    "clip_top_n": 5,
    "clip_history": 3,
    # chat templates
    "template": "◇ Shoutout to @{name}! They were last seen streaming {game}. Show some love → twitch.tv/{login}",
    "template_raid": "◇ Thank you for the raid, @{name}! ({viewers}) They were last streaming {game} → twitch.tv/{login}",
    "template_live": "◇ @{name} is LIVE right now playing {game}! Go show some love → twitch.tv/{login}",
    "template_notfound": "◇ Couldn't find a Twitch channel called @{login} to shout out.",
}


def cfg(key):
    c = config.get("shoutout")
    c = c if isinstance(c, dict) else {}
    return c.get(key, DEFAULTS.get(key))


# ── Helix / GQL (stdlib) ───────────────────────────────────────────────────
def _helix_get(path, params):
    """GET a Helix endpoint, returning its `data` list. Retries once on 401."""
    from . import twitch
    for attempt in (1, 2):
        token = twitch.get_access_token()
        if not token:
            return []
        url = f"https://api.twitch.tv/helix/{path}?{urllib.parse.urlencode(params, doseq=True)}"
        req = urllib.request.Request(url, headers={
            "Client-ID": TWITCH_CLIENT_ID, "Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return (json.loads(r.read() or b"{}") or {}).get("data", [])
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 1:
                twitch.invalidate_token()
                continue
            print(f"[shoutout] helix {path} failed: {e.code}")
            return []
        except Exception as e:
            print(f"[shoutout] helix {path} error: {e}")
            return []
    return []


_GQL_URL = "https://gql.twitch.tv/gql"
_GQL_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"   # Twitch's public web client id
_CLIP_Q = ('query($slug: ID!){ clip(slug: $slug){ durationSeconds '
           'playbackAccessToken(params: {platform: "web", playerBackend: "mediaplayer", playerType: "site"})'
           '{ signature value } videoQualities{ quality sourceURL } } }')


def _clip_mp4(slug):
    """Resolve a clip slug to the signed mp4 the Twitch site plays. ("", 0) on failure."""
    if not slug:
        return "", 0.0
    body = json.dumps({"query": _CLIP_Q, "variables": {"slug": slug}}).encode()
    req = urllib.request.Request(_GQL_URL, data=body, headers={
        "Client-ID": _GQL_CLIENT_ID, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            clip = ((json.loads(r.read() or b"{}") or {}).get("data") or {}).get("clip") or {}
    except Exception as e:
        print(f"[shoutout] clip gql failed: {e}")
        return "", 0.0
    quals = clip.get("videoQualities") or []
    tok = clip.get("playbackAccessToken") or {}
    dur = float(clip.get("durationSeconds") or 0)
    if not quals or not tok.get("signature"):
        return "", dur
    pick = next((q for q in quals if str(q.get("quality")) == "720"), quals[0])
    return pick["sourceURL"] + "?sig=" + tok["signature"] + "&token=" + urllib.parse.quote(tok["value"]), dur


# ── clip selection (recent tier, then popular tier; avoids repeats) ────────
_last_clips = {}          # login -> deque of recently shown clip ids
_MAX_TRACKED = 500


def _seen(login):
    """Per-streamer clip memory, LRU so frequent guests aren't forgotten first."""
    dq = _last_clips.pop(login, None)
    if dq is None:
        while len(_last_clips) >= _MAX_TRACKED:
            _last_clips.pop(next(iter(_last_clips)))
        dq = deque(maxlen=max(int(cfg("clip_history")), 1))
    _last_clips[login] = dq
    return dq


def _pick(clips, login, key, pool_size, newest_first):
    if not clips:
        return None
    ordered = sorted(clips, key=key, reverse=True)[:pool_size]
    seen = _seen(login)
    choices = [c for c in ordered if c.get("id") not in seen] or ordered
    chosen = random.choice(choices)
    seen.append(chosen.get("id"))
    return chosen


def _fetch_clips(uid, days):
    """Clips in a window. Twitch needs BOTH bounds or it assumes a 1-week span."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return _helix_get("clips", {
        "broadcaster_id": uid, "first": 100,
        "started_at": (now - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ended_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    })


def _resolve_clip(uid, login):
    """(clip_id, mp4_url, thumb, duration) — newest-recent tier, else popular tier."""
    try:
        c = _pick(_fetch_clips(uid, int(cfg("clip_recent_days"))), login,
                  lambda x: x.get("created_at", ""), int(cfg("clip_recent_pool")), True)
        if not c:
            c = _pick(_fetch_clips(uid, int(cfg("clip_popular_days"))), login,
                      lambda x: x.get("view_count", 0) or 0, int(cfg("clip_top_n")), False)
        if not c:
            return "", "", "", 0.0
        cid, thumb = c.get("id", ""), c.get("thumbnail_url", "")
        dur = float(c.get("duration", 0) or 0)
        mp4, gql_dur = _clip_mp4(cid)
        if gql_dur:
            dur = gql_dur
        if not mp4 and "-preview-" in thumb:      # legacy fallback for old clips
            mp4 = thumb.split("-preview-")[0] + ".mp4"
        return cid, mp4, thumb, dur
    except Exception as e:
        print(f"[shoutout] clip lookup error: {e}")
        return "", "", "", 0.0


def _hold_ms(clip_dur, mp4):
    if clip_dur:
        return int(min(max(clip_dur, 6.0), 40.0) * 1000) + 1400
    return int(cfg("hold_ms")) if mp4 else int(cfg("noclip_hold_ms"))


def lookup(login):
    """Build the overlay payload for a login, or None if the user doesn't exist."""
    login = (login or "").lstrip("@").strip().lower()
    if not login:
        return None
    users = _helix_get("users", {"login": login})
    if not users:
        return None
    u = users[0]
    uid = u["id"]
    game = ""
    chans = _helix_get("channels", {"broadcaster_id": uid})
    if chans:
        game = chans[0].get("game_name") or ""
    live = False
    streams = _helix_get("streams", {"user_id": uid})
    if streams:
        live = True
        game = streams[0].get("game_name") or game
    cid, mp4, thumb, dur = _resolve_clip(uid, login)
    return {
        "name": u.get("display_name") or login, "login": u.get("login") or login,
        "avatar": u.get("profile_image_url", ""), "offline": u.get("offline_image_url", ""),
        "category": game, "live": live,
        "clip": mp4, "thumb": thumb, "clipId": cid,
        "hold": _hold_ms(dur, mp4),
        "volume": float(cfg("clip_volume")), "fadeMs": int(cfg("clip_fade_in_ms")),
        "noclipHold": int(cfg("noclip_hold_ms")),
    }


# ── guards / queue model ───────────────────────────────────────────────────
_lock = threading.Lock()
_until = {}               # login -> ts before which a repeat is refused
_reserved = set()         # guards created by a booked card (so "clear" frees them)
_screen_free_at = 0.0     # when the overlay finishes everything queued
_MAX_GUARDS = 512
_state = {"enabled": True, "pending_raid": None, "last": None}

_URL_RE = re.compile(r"^(?:https?://)?(?:www\.)?twitch\.tv/([^/?#\s]+)", re.I)


def _norm(login):
    v = (login or "").strip()
    m = _URL_RE.match(v)
    if m:
        v = m.group(1)
    return v.lstrip("@").strip().lower()


def _prune(now):
    if len(_until) < 256:
        return
    for k in [k for k, v in _until.items() if v <= now]:
        _until.pop(k, None)
        _reserved.discard(k)
    if len(_until) > _MAX_GUARDS:
        for k, _ in sorted(_until.items(), key=lambda kv: kv[1])[:len(_until) - _MAX_GUARDS]:
            _until.pop(k, None)
            _reserved.discard(k)


def _reserve_screen(login, hold_ms):
    """Book the card's screen time and guard the login until well after it leaves."""
    global _screen_free_at
    now = time.time()
    start = max(now, _screen_free_at)
    _screen_free_at = start + (hold_ms / 1000.0) + 0.7      # + exit fade
    _until[login] = _screen_free_at + int(cfg("repeat_guard_sec"))
    _reserved.add(login)


def _release_screen():
    global _screen_free_at
    _screen_free_at = time.time()
    for k in list(_reserved):
        _until.pop(k, None)
    _reserved.clear()


def _viewers_phrase(n):
    return "" if not n else "%d viewer%s" % (n, "" if n == 1 else "s")


def _log_history(entry):
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        entry["ts"] = time.time()
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[shoutout] could not write log: {e}")


def restore_clip_history():
    """Rehydrate clip rotation from the log so a restart doesn't replay clips."""
    if not os.path.isfile(LOG_FILE):
        return 0
    per = {}
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("login") and d.get("clipId"):
                    per.setdefault(d["login"], []).append(d["clipId"])
    except Exception:
        return 0
    for login, ids in per.items():
        dq = _seen(login)
        for cid in ids[-int(cfg("clip_history")):]:
            if cid and cid not in dq:
                dq.append(cid)
    return len(per)


# ── the one action ─────────────────────────────────────────────────────────
def do_shoutout(login, source="command", viewers=0, say=None):
    """Safety gates → lookup → overlay card → chat line. Never raises."""
    try:
        return _shoutout(_norm(login), source, int(viewers or 0), say)
    except Exception as e:
        print(f"[shoutout] failed for {login}: {e}")
        return False


def _shoutout(login, source, viewers, say):
    if not login or not cfg("enabled") or not _state["enabled"]:
        return False
    if login in {b.strip().lower() for b in (cfg("blocklist") or [])}:
        print(f"[shoutout] blocklisted — refusing {login}")
        return False
    if login == (TWITCH_USER or "").lower() and not cfg("allow_self"):
        return False
    if source == "raid":
        allow = {a.strip().lower() for a in (cfg("raid_allowlist") or [])}
        if allow and login not in allow:
            return False
        if viewers < int(cfg("raid_min_viewers")):
            return False
        if cfg("raid_require_approval"):
            _state["pending_raid"] = {"login": login, "viewers": viewers, "at": time.time()}
            if say:
                say(f"◇ Raid from {login} is waiting for a mod to approve — type !so ok")
            return False

    now = time.time()
    with _lock:
        _prune(now)
        if now < _until.get(login, 0.0):
            return False                                   # still on screen / just left
        if max(0.0, _screen_free_at - now) > int(cfg("max_queue_sec")):
            print(f"[shoutout] overlay backed up — dropping {login}")
            return False
        # guard the whole lookup (several Helix calls), not just the cooldown
        _until[login] = now + int(cfg("lookup_guard_sec"))

    data = lookup(login)
    if not data:
        with _lock:
            _until[login] = time.time() + int(cfg("cooldown_sec"))
        if say and cfg("chat_send"):
            say(cfg("template_notfound").format(login=login))
        return False

    is_raid = source in ("raid", "approved")
    data["raid"] = is_raid
    data["raiders"] = viewers if is_raid else 0

    effects.emit("shoutout", data,
                 summary=f"◇ Shoutout: {data['name']}" + (f" (raid {viewers})" if is_raid else ""))
    with _lock:
        _reserve_screen(login, data.get("hold") or int(cfg("hold_ms")))
    _state["last"] = {"name": data["name"], "login": data["login"], "at": time.time(),
                      "source": source, "live": bool(data.get("live"))}
    _log_history({"login": data["login"], "name": data["name"], "source": source,
                  "raiders": data["raiders"], "live": bool(data.get("live")),
                  "category": data.get("category") or "", "clipId": data.get("clipId") or "",
                  "hasClip": bool(data.get("clip"))})

    if say and cfg("chat_send"):
        tmpl = cfg("template_raid") if is_raid else (cfg("template_live") if data.get("live") else cfg("template"))
        say(tmpl.format(name=data["name"], login=data["login"],
                        game=data.get("category") or "content", viewers=_viewers_phrase(viewers)))
    return True


def control(action, say=None):
    """Mod subcommands: skip · clear · off · on · ok · status."""
    action = (action or "").lower()
    if action in ("skip", "clear"):
        effects.emit("shoutout", {"control": action})
        if action == "clear":
            with _lock:
                _release_screen()
        if say:
            say("◇ Cleared the shoutout queue." if action == "clear" else "◇ Skipped the current card.")
        return True
    if action in ("off", "on"):
        _state["enabled"] = (action == "on")
        if say:
            say(f"◇ Shoutouts are now {'ON' if _state['enabled'] else 'OFF'}.")
        return True
    if action == "ok":
        pending, _state["pending_raid"] = _state["pending_raid"], None
        if not pending:
            if say:
                say("◇ Nothing is waiting for approval.")
        elif time.time() - pending["at"] > int(cfg("raid_approval_ttl")):
            if say:
                say("◇ That raid approval expired.")
        else:
            _shoutout(pending["login"], "approved", pending["viewers"], say)
        return True
    if action == "status":
        if say:
            p = _state["pending_raid"]
            say(f"◇ Shoutouts {'on' if _state['enabled'] else 'off'} · pending raid: "
                f"{p['login'] if p else 'none'}")
        return True
    return False


def on_raid(login, viewers, say=None):
    """Hook for EventSub channel.raid."""
    if not cfg("raid_shoutout"):
        return False
    return do_shoutout(login, "raid", viewers, say)


# ── clip playback state (the overlay reports it; drives OBS ducking) ───────
_clip = {"playing": False, "at": 0.0}


def set_clip_playing(active):
    """Called when the overlay starts/stops a clip. Hook point for audio ducking."""
    _clip["playing"] = bool(active)
    _clip["at"] = time.time()
    return dict(_clip)


def clip_state():
    return dict(_clip)


def public_status():
    return {
        "enabled": bool(cfg("enabled")) and _state["enabled"],
        "mods_only": bool(cfg("mods_only")),
        "raid_shoutout": bool(cfg("raid_shoutout")),
        "pending_raid": _state["pending_raid"],
        "last": _state["last"],
        "clip": clip_state(),
    }


def demo_card():
    """A sample card so the dashboard can test the overlay without a Helix call."""
    return {
        "name": "PixelWitch", "login": "pixelwitch", "category": "Hollow Knight: Silksong",
        "avatar": "", "offline": "", "live": False,
        "clip": "", "thumb": "", "clipId": "",
        "hold": int(cfg("noclip_hold_ms")), "volume": float(cfg("clip_volume")),
        "fadeMs": int(cfg("clip_fade_in_ms")), "noclipHold": int(cfg("noclip_hold_ms")),
        "raid": False, "raiders": 0,
    }
