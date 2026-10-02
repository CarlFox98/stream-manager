"""Wheel outcomes that actually do something — and only when they can.

Each wheel segment names an `outcome` from OUTCOMES below. An outcome has:

  requires  conditions checked at spin time. A segment whose conditions fail is
            left off the wheel entirely, so a viewer never sees or lands on
            something that cannot happen right now (a shoutout while offline,
            a Spotify pick with nothing playing, VIP for a moderator).
  run       does the thing. Returns (ok, message). ok=False refunds the
            redemption and tells chat why.

Anything the streamer has to do by hand is put on the owed list (owed.py) so it
can't be forgotten. Anything that turns something ON goes through timed.py so
it turns itself OFF again — on its timer, at shutdown, or after a crash.

Old configs that use a bare "action" (vip / timeout:60 / shoutout / scene:x)
still work through actions.py; see games.spin_wheel.
"""
import random, threading, time

from . import featured, helix, owed, timed
from .config import config


def _state():
    from .state import state   # lazy: importing state starts its poll thread
    return state

# ── settings ───────────────────────────────────────────────────────────────
_DEFAULTS = {
    "outcome_delay": 5.5,          # seconds: land the effect when the wheel stops
    "vip_days": 7,
    "dj_window": 120,              # seconds the winner has to paste a link
    "dj_max_minutes": 10,
    "dj_allow_explicit": True,
    "poll_seconds": 120,
    "poll_title": "What should I play next?",
    "prediction_seconds": 120,
    "prediction_title": "Will I win the next round?",
    "emote_party_seconds": 120,
    "slow_mode_seconds": 120,
    "slow_mode_wait": 30,
    "flip_seconds": 60,
    "tint_seconds": 120,
    "swap_seconds": 300,
    "silly_voice_seconds": 300,
    "timeout_seconds": 60,
}


def opt(key):
    c = config.get("outcomes") if isinstance(config.get("outcomes"), dict) else {}
    return c.get(key, _DEFAULTS[key])


# ── requirement checks (cheap, cached where they hit the network) ──────────
_cache = {}


def _cached(key, ttl, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception:
        val = False
    _cache[key] = (now, val)
    return val


def _is_live():
    return bool(_state().get("twitch", {}).get("live"))


def _is_streamer(user_id):
    """Has this viewer ever set up a channel to stream? (category set)"""
    def go():
        code, body = helix.request("GET", "channels", {"broadcaster_id": user_id})
        return code == 200 and bool((body.get("data") or [{}])[0].get("game_name"))
    return _cached(("streamer", user_id), 3600, go)


def _is_mod(user_id):
    def go():
        code, body = helix.request("GET", "moderation/moderators",
                                   {"broadcaster_id": helix.broadcaster_id(), "user_id": user_id})
        return code == 200 and bool(body.get("data"))
    return _cached(("mod", user_id), 300, go)


def _is_vip(user_id):
    def go():
        code, body = helix.request("GET", "channels/vips",
                                   {"broadcaster_id": helix.broadcaster_id(), "user_id": user_id})
        return code == 200 and bool(body.get("data"))
    return _cached(("vip", user_id), 60, go)


def _our_vip(user_id):
    return any(e["kind"] == "vip" and e["target"] == user_id for e in timed.active())


def _vip_ok(ctx):
    uid = ctx.get("user_id")
    if not uid or uid == helix.broadcaster_id() or _is_mod(uid):
        return False
    return (not _is_vip(uid)) or _our_vip(uid)   # a VIP we granted can be extended


def _spotify_ok():
    from . import spotify
    return spotify.connected() and _cached("sp_device", 20, spotify.has_active_device)


def _steam_ok():
    from . import steam_library
    return len(steam_library.games()) >= 2


def _obs_ok():
    from . import obs_control
    return _cached("obs_ok", 15, obs_control.available)


def _sets_ok():
    from . import scenes
    return len(scenes.available_sets()) >= 2


CHECKS = {
    "live":        lambda ctx: _is_live(),
    "target":      lambda ctx: bool(ctx.get("user_id")) and ctx.get("user_id") != helix.broadcaster_id(),
    "streamer":    lambda ctx: bool(ctx.get("user_id")) and _is_streamer(ctx["user_id"]),
    "not_mod":     lambda ctx: bool(ctx.get("user_id")) and not _is_mod(ctx["user_id"]),
    "vip_ok":      _vip_ok,
    "spotify":     lambda ctx: _spotify_ok(),
    "steam":       lambda ctx: _steam_ok(),
    "obs":         lambda ctx: _obs_ok(),
    "overlay_sets": lambda ctx: _sets_ok(),
}


def eligible(outcome_name, ctx):
    o = OUTCOMES.get(outcome_name)
    if not o:
        return False
    for req in o["requires"]:
        fn = CHECKS.get(req)
        if fn is None or not fn(ctx):
            return False
    return True


def why_not(outcome_name, ctx):
    o = OUTCOMES.get(outcome_name)
    if not o:
        return ["unknown outcome"]
    return [r for r in o["requires"] if not (CHECKS.get(r) and CHECKS[r](ctx))]


# ── Twitch helpers ─────────────────────────────────────────────────────────
def _bm():
    b = helix.broadcaster_id()
    return {"broadcaster_id": b, "moderator_id": b}


def announce(message, color="primary"):
    code, body = helix.request("POST", "chat/announcements", _bm(),
                               {"message": message[:500], "color": color})
    return helix.ok(code)


def _say(ctx, text):
    if ctx.get("say"):
        try:
            ctx["say"](text)
        except Exception:
            pass


# ── timed kinds ────────────────────────────────────────────────────────────
def _chat_capture(target, params):
    code, body = helix.request("GET", "chat/settings", _bm())
    if code != 200 or not body.get("data"):
        raise RuntimeError(helix.error_text(code, body))
    d = body["data"][0]
    return {k: d.get(k) for k in ("emote_mode", "slow_mode", "slow_mode_wait_time")}


def _chat_patch(changes):
    code, body = helix.request("PATCH", "chat/settings", _bm(), changes)
    return helix.ok(code), ("" if helix.ok(code) else helix.error_text(code, body))


def _emote_apply(target, params, snap):
    return _chat_patch({"emote_mode": True})


def _emote_revert(target, params, snap):
    return _chat_patch({"emote_mode": bool((snap or {}).get("emote_mode"))})[0]


def _slow_apply(target, params, snap):
    return _chat_patch({"slow_mode": True, "slow_mode_wait_time": int(params.get("wait", 30))})


def _slow_revert(target, params, snap):
    snap = snap or {}
    if snap.get("slow_mode"):
        return _chat_patch({"slow_mode": True,
                            "slow_mode_wait_time": int(snap.get("slow_mode_wait_time") or 30)})[0]
    return _chat_patch({"slow_mode": False})[0]


def _vip_apply(target, params, snap):
    code, body = helix.request("POST", "channels/vips",
                               {"broadcaster_id": helix.broadcaster_id(), "user_id": target})
    if code == 422 or code == 409:          # already VIP (granted by us earlier)
        return True, "already VIP"
    return helix.ok(code), ("" if helix.ok(code) else helix.error_text(code, body))


def _vip_revert(target, params, snap):
    code, body = helix.request("DELETE", "channels/vips",
                               {"broadcaster_id": helix.broadcaster_id(), "user_id": target})
    _cache.pop(("vip", target), None)
    # 422/404 = no longer a VIP (removed by hand) — nothing left to undo
    return helix.ok(code) or code in (404, 422)


def _swap_capture(target, params):
    from . import scenes
    return {"set": scenes.detect_active_set()}


def _swap_apply(target, params, snap):
    from . import scenes
    ok, msg = scenes.apply_scene_set(params["to"])
    return ok, msg


def _swap_revert(target, params, snap):
    from . import scenes
    original = (snap or {}).get("set")
    if not original:
        return True
    if scenes.detect_active_set() != params.get("to"):
        return True                     # streamer switched by hand meanwhile — leave it
    return scenes.apply_scene_set(original)[0]


def _noop_apply(target, params, snap):
    return True, ""


def _noop_revert(target, params, snap):
    return True


def register_timed_kinds():
    from . import obs_control
    timed.register("emote_mode", _emote_apply, _emote_revert, capture=_chat_capture, label="Emote-only chat")
    timed.register("slow_mode", _slow_apply, _slow_revert, capture=_chat_capture, label="Slow mode")
    timed.register("vip", _vip_apply, _vip_revert, long=True, label="VIP")
    timed.register("overlay_swap", _swap_apply, _swap_revert, capture=_swap_capture, label="Overlay swap")
    timed.register("tuber_flip", obs_control.apply_flip, obs_control.revert_transform,
                   capture=obs_control.capture_transform, label="Upside-down")
    timed.register("tuber_tint", obs_control.apply_tint, obs_control.revert_tint,
                   capture=obs_control.capture_tint, label="Cursed tint")
    timed.register("countdown", _noop_apply, _noop_revert, label="Challenge")


# ── DJ window (chat hook) ──────────────────────────────────────────────────
_dj = {}            # login -> {"user", "until"}
_dj_lock = threading.Lock()


def _dj_expire(login):
    with _dj_lock:
        w = _dj.get(login)
        if not w or w["until"] > time.time() + 0.5:
            return
        _dj.pop(login, None)
    owed.add(f"Song pick for {w['user']} (their 2-minute window ran out)", user=w["user"],
             source="lucky:dj")
    if w.get("say"):
        try:
            w["say"](f"🎧 @{w['user']} your DJ window closed — Neo will sort your song pick by hand.")
        except Exception:
            pass


def on_chat(login, user, text, say=None):
    """Called for every chat line. Returns True if it consumed a DJ pick."""
    login = (login or "").lower()
    with _dj_lock:
        w = _dj.get(login)
        if not w or w["until"] < time.time():
            return False
    from . import spotify
    tid = spotify.parse_track_id(text)
    if not tid:
        return False
    t = spotify.track(tid)
    if not t:
        say and say(f"🎧 @{user} that link didn't resolve to a Spotify track — try another (track links only).")
        return True
    if t["duration_ms"] > int(opt("dj_max_minutes")) * 60000:
        say and say(f"🎧 @{user} that one's over {opt('dj_max_minutes')} minutes — pick something shorter.")
        return True
    if t["explicit"] and not opt("dj_allow_explicit"):
        say and say(f"🎧 @{user} explicit tracks are off for this stream — pick another.")
        return True
    ok, why = spotify.queue_track(t["uri"])
    if not ok:
        say and say(f"🎧 @{user} couldn't queue it: {why}")
        return True
    with _dj_lock:
        _dj.pop(login, None)
    say and say(f"🎧 Queued for @{user}: {t['title']} — {t['artist']}")
    return True


# ── the outcomes ───────────────────────────────────────────────────────────
def _featured(ctx):
    return featured.add(ctx["user"], ctx.get("user_id", ""), ctx.get("login", ""))


def _dj_start(ctx):
    login = (ctx.get("login") or ctx["user"]).lower()
    secs = int(opt("dj_window"))
    with _dj_lock:
        _dj[login] = {"user": ctx["user"], "until": time.time() + secs, "say": ctx.get("say")}
    threading.Timer(secs + 1, _dj_expire, args=(login,)).start()
    _say(ctx, f"🎧 @{ctx['user']} you're the DJ! Paste a Spotify track link in chat within "
              f"{secs // 60} min and it goes straight into the queue.")
    return True, "dj window open"


def _vip7(ctx):
    days = float(opt("vip_days"))
    ok, msg, entry = timed.start("vip", ctx["user_id"], days * 86400, params={"name": ctx["user"]},
                                 label=f"VIP: {ctx['user']}", user=ctx["user"])
    if ok:
        word = "extended" if msg == "extended" else "granted"
        _say(ctx, f"⭐ VIP {word} for {ctx['user']} — {int(days)} days!")
        announce(f"⭐ {ctx['user']} won VIP for {int(days)} days on the Lucky Wheel!", "purple")
    return ok, msg


def _game_poll(ctx):
    from . import steam_library
    choices = steam_library.pick_choices(5, exclude_name=_state().get("twitch", {}).get("game", ""))
    if len(choices) < 2:
        return False, "not enough installed games to vote on"
    secs = max(15, min(int(opt("poll_seconds")), 1800))
    code, body = helix.request("POST", "polls", None, {
        "broadcaster_id": helix.broadcaster_id(), "title": str(opt("poll_title"))[:60],
        "choices": [{"title": c["label"]} for c in choices], "duration": secs})
    if not helix.ok(code) or not body.get("data"):
        return False, helix.error_text(code, body)
    poll_id = body["data"][0]["id"]
    item = owed.add(f"Chat is voting on the next game ({ctx['user']}'s Lucky spin)",
                    user=ctx["user"], source="lucky:game_poll",
                    detail=", ".join(c["label"] for c in choices))
    _say(ctx, f"🎮 {ctx['user']} lets chat pick the next game — vote now!")
    threading.Timer(secs + 5, _poll_result, args=(poll_id, item["id"])).start()
    return True, "poll open"


def _poll_result(poll_id, owed_id):
    code, body = helix.request("GET", "polls", {"broadcaster_id": helix.broadcaster_id(), "id": poll_id})
    if code != 200 or not body.get("data"):
        return
    ch = body["data"][0].get("choices") or []
    if not ch:
        return
    best = max(ch, key=lambda c: (c.get("votes") or 0))
    owed.update(owed_id, text=f"Chat picked the next game: {best.get('title')}")
    try:
        from . import chat
        chat.say(f"🎮 Chat has spoken — next game: {best.get('title')}!")
    except Exception:
        pass


def _emote_party(ctx):
    ok, msg, _ = timed.start("emote_mode", "chat", int(opt("emote_party_seconds")),
                             label="Emote-only chat", user=ctx["user"])
    if ok:
        _say(ctx, f"🥳 EMOTE PARTY courtesy of {ctx['user']} — emotes only for "
                  f"{int(opt('emote_party_seconds')) // 60} min!")
    return ok, msg


def _name_run(ctx):
    owed.add(f"{ctx['user']} names your next run / character / save", user=ctx["user"], source="lucky:name_run")
    _say(ctx, f"✍️ @{ctx['user']} reply in chat with a name — it goes on Neo's list!")
    return True, "owed"


def _shoutout(ctx):
    from . import actions
    ok = actions._do_shoutout(ctx["user"], ctx.get("user_id", ""), ctx.get("say"))
    return ok, "" if ok else "Twitch refused the shoutout (cooldown or offline)"


def _jackpot(ctx):
    done = []
    if _featured(ctx)[0]:
        done.append("Featured")
    if ctx.get("user_id") and _vip_ok(ctx) and _vip7(ctx)[0]:
        done.append(f"VIP {int(opt('vip_days'))} days")
    if not done:
        return False, "jackpot could not grant anything"
    announce(f"🎉 JACKPOT for {ctx['user']}: {' + '.join(done)}!", "orange")
    _say(ctx, f"🎉 JACKPOT! {ctx['user']} gets {' + '.join(done)}!")
    return True, "jackpot"


def _flip(ctx):
    from . import obs_control
    src = obs_control.cfg()["source"]
    ok, msg, _ = timed.start("tuber_flip", src, int(opt("flip_seconds")), label="Upside-down", user=ctx["user"])
    if ok:
        _say(ctx, f"🙃 {ctx['user']} flipped Neo upside down for {int(opt('flip_seconds'))}s!")
    return ok, msg


def _tint(ctx):
    from . import obs_control
    src = obs_control.cfg()["source"]
    ok, msg, _ = timed.start("tuber_tint", src, int(opt("tint_seconds")), label="Cursed tint", user=ctx["user"])
    if ok:
        _say(ctx, f"🧪 {ctx['user']} cursed Neo's colours for {int(opt('tint_seconds')) // 60} min!")
    return ok, msg


def _swap(ctx):
    from . import scenes
    current = scenes.detect_active_set()
    others = [s for s in scenes.available_sets() if s != current]
    if not others:
        return False, "no other overlay set to swap to"
    to = random.choice(others)
    ok, msg, _ = timed.start("overlay_swap", "overlay", int(opt("swap_seconds")), params={"to": to},
                             label=f"Overlay: {to}", user=ctx["user"])
    if ok:
        _say(ctx, f"🎨 {ctx['user']} swapped the overlay to {to} for {int(opt('swap_seconds')) // 60} min!")
    return ok, msg


def _slow(ctx):
    ok, msg, _ = timed.start("slow_mode", "chat", int(opt("slow_mode_seconds")),
                             params={"wait": int(opt("slow_mode_wait"))}, label="Slow mode", user=ctx["user"])
    if ok:
        _say(ctx, f"🐌 {ctx['user']} put chat in slow mode ({int(opt('slow_mode_wait'))}s) for "
                  f"{int(opt('slow_mode_seconds')) // 60} min!")
    return ok, msg


def _bets(ctx):
    secs = max(30, min(int(opt("prediction_seconds")), 1800))
    title = str(opt("prediction_title"))[:45]
    code, body = helix.request("POST", "predictions", None, {
        "broadcaster_id": helix.broadcaster_id(), "title": title,
        "outcomes": [{"title": "Yes"}, {"title": "No"}], "prediction_window": secs})
    if not helix.ok(code):
        return False, helix.error_text(code, body)
    owed.add(f"Resolve the prediction \"{title}\" on Twitch", user=ctx["user"], source="risky:bets")
    _say(ctx, f"🎲 {ctx['user']} opened a prediction — place your bets!")
    return True, "prediction open"


def _silly(ctx):
    secs = int(opt("silly_voice_seconds"))
    timed.start("countdown", f"silly:{time.time():.0f}", secs, label="Silly voice", user=ctx["user"])
    _say(ctx, f"🤪 {ctx['user']} says: silly voice for {secs // 60} minutes, starting NOW!")
    return True, "countdown"


def _pun(ctx):
    owed.add(f"Read a bad pun for {ctx['user']}", user=ctx["user"], source="risky:pun")
    return True, "owed"


def _timeout(ctx):
    from . import actions
    secs = int(opt("timeout_seconds"))
    code, body = helix.request("POST", "moderation/bans", _bm(), {"data": {
        "user_id": ctx["user_id"], "duration": secs, "reason": "Risky Wheel outcome"}})
    if helix.ok(code):
        _say(ctx, f"⏱️ {ctx['user']} got a {secs}s timeout from the Risky Wheel!")
        return True, "timed out"
    _ = actions   # legacy module kept for "action" specs
    return False, helix.error_text(code, body)


def _nothing(ctx):
    _say(ctx, f"😈 Nothing happens to {ctx['user']}… this time.")
    return True, "nothing"


OUTCOMES = {
    # lucky
    "featured":    {"requires": [],                     "run": _featured},
    "dj":          {"requires": ["spotify"],            "run": _dj_start},
    "vip":         {"requires": ["target", "vip_ok"],   "run": _vip7},
    "game_poll":   {"requires": ["live", "steam"],      "run": _game_poll},
    "emote_party": {"requires": [],                     "run": _emote_party},
    "name_run":    {"requires": [],                     "run": _name_run},
    "shoutout":    {"requires": ["live", "target", "streamer"], "run": _shoutout},
    "jackpot":     {"requires": [],                     "run": _jackpot},
    # risky
    "tuber_flip":  {"requires": ["obs"],                "run": _flip},
    "cursed_tint": {"requires": ["obs"],                "run": _tint},
    "overlay_swap": {"requires": ["overlay_sets"],      "run": _swap},
    "slow_mode":   {"requires": [],                     "run": _slow},
    "chat_bets":   {"requires": ["live"],               "run": _bets},
    "silly_voice": {"requires": [],                     "run": _silly},
    "bad_pun":     {"requires": [],                     "run": _pun},
    "timeout":     {"requires": ["target", "not_mod"],             "run": _timeout},
    "nothing":     {"requires": [],                     "run": _nothing},
}


def run(outcome_name, ctx):
    o = OUTCOMES.get(outcome_name)
    if not o:
        return False, f"unknown outcome '{outcome_name}'"
    try:
        return o["run"](ctx)
    except Exception as e:
        print(f"[outcomes] {outcome_name} failed: {e}")
        return False, str(e)
