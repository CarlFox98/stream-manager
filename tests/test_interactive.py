"""Logic tests for the interactive layer (games, cooldowns, redeems, actions,
stats, persistence). No network or live Twitch required.

Run from the repo root:   python -m pytest -q
"""
import random
import pytest

from stream_manager import (games, cooldowns, effects, quotes, redeems,
                             actions, stats, eventsub, twitch_auth, commands, config as cfg)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Point every on-disk store at a temp dir and reset in-memory state."""
    monkeypatch.setattr(quotes, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(quotes, "QUOTES_FILE", str(tmp_path / "quotes.json"))
    quotes._loaded = False
    quotes._store = {"next_id": 1, "quotes": []}
    monkeypatch.setattr(stats, "STATS_FILE", str(tmp_path / "stats.json"))
    stats._loaded = False
    stats._store = {"totals": {}, "users": {}, "wheel_outcomes": {}, "jackpots": 0, "recent": []}
    monkeypatch.setattr(cooldowns, "_FILE", str(tmp_path / "cd.json"))
    monkeypatch.setattr(games, "_SPIN_FILE", str(tmp_path / "spin.json"))
    cooldowns.reset()
    games._spin_history.clear()
    random.seed(1234)
    yield


# ── games ──────────────────────────────────────────────────────────────────
def test_coinflip_fair():
    from collections import Counter
    c = Counter(games.coinflip("u") for _ in range(8000))
    assert 0.45 < c["HEADS"] / 8000 < 0.55


def test_5050_fair():
    from collections import Counter
    c = Counter(games.fifty_fifty("u") for _ in range(8000))
    assert 0.45 < c["WIN"] / 8000 < 0.55


def test_slots_and_dice_and_8ball_and_duel():
    assert len(games.slots("u")) >= 3
    assert 1 <= games.dice("u", sides=20) <= 20
    assert isinstance(games.eight_ball("q?", "u"), str)
    assert games.duel("a", "", None) is None
    assert games.duel("a", "@b", None) in ("a", "b")


def test_weighted_pick_tracks_weight():
    segs = games._wheel_cfg("lucky")["segments"]
    total = sum(s.get("weight", 1) for s in segs)
    from collections import Counter
    picks = Counter(games._pick_weighted(segs) for _ in range(40000))
    assert abs(picks[0] / 40000 - segs[0].get("weight", 1) / total) < 0.03


# ── wheel anti-spam ─────────────────────────────────────────────────────────
def test_wheel_no_repeat():
    segs = games._wheel_cfg("risky")["segments"]
    prev, repeats = None, 0
    for _ in range(300):
        idx = games._pick_wheel("risky", segs, "alice")
        repeats += (idx == prev)
        prev = idx
    assert repeats == 0


def test_wheel_severe_cooldown():
    import time
    segs = games._wheel_cfg("risky")["segments"]
    sev = [i for i, s in enumerate(segs) if s.get("severe")]
    assert sev, "risky wheel should have a severe slot"
    games._spin_history[("risky", "carol")] = {"last_index": sev[0], "last_severe_ts": time.time()}
    hits = sum(segs[games._pick_wheel("risky", segs, "carol")].get("severe", False) for _ in range(400))
    assert hits == 0


# ── cooldowns ────────────────────────────────────────────────────────────────
def test_cooldown_blocks_then_records():
    ok1, _, _ = cooldowns.check("coinflip", "bob", 30, 3)
    ok2, wait, scope = cooldowns.check("coinflip", "bob", 30, 3)
    assert ok1 and not ok2 and scope in ("global", "user") and wait > 0


def test_mods_bypass(monkeypatch):
    monkeypatch.setitem(cfg.config, "cooldowns", {"mods_bypass": True})
    sent = []
    for _ in range(3):
        games.handle_command("!coinflip", "mod", is_mod=True, say=sent.append)
    assert sum("flipped" in s for s in sent) == 3


def test_cooldown_persist():
    import time as _t
    now = _t.time()
    cooldowns._last_global["coinflip"] = now
    cooldowns._last_user[("coinflip", "x")] = now - 5
    cooldowns.save(); cooldowns.reset(); cooldowns.load()
    assert cooldowns._last_global.get("coinflip") == now
    assert cooldowns._last_user.get(("coinflip", "x")) == now - 5


def test_cooldown_prunes_stale_entries():
    """Entries older than _MAX_AGE must not be persisted or reloaded — otherwise
    cooldowns.json gains a permanent row per (action, viewer), forever."""
    import time as _t
    cooldowns.reset()
    now = _t.time()
    cooldowns._last_user[("coinflip", "fresh")] = now - 10
    cooldowns._last_user[("coinflip", "ancient")] = now - cooldowns._MAX_AGE - 60
    cooldowns.save(); cooldowns.reset(); cooldowns.load()
    assert ("coinflip", "fresh") in cooldowns._last_user
    assert ("coinflip", "ancient") not in cooldowns._last_user
    cooldowns.reset()


# ── quotes ───────────────────────────────────────────────────────────────────
def test_quote_crud():
    q1 = quotes.add("gg", added_by="a")
    q2 = quotes.add("wp", added_by="b")
    assert (q1["id"], q2["id"]) == (1, 2)
    assert quotes.delete(1) and quotes.get(1) is None and quotes.get(2)["text"] == "wp"


# ── effects ──────────────────────────────────────────────────────────────────
def test_effects_since():
    h = effects.head("wheel")
    a = effects.emit("wheel", {"x": 1})
    b = effects.emit("wheel", {"x": 2})
    got = effects.since("wheel", h)
    assert [e["id"] for e in got["events"]] == [a, b]
    assert effects.since("wheel", b)["events"] == []


# ── reward limits ────────────────────────────────────────────────────────────
def test_reward_limits_builder():
    lim = redeems._reward_limits({"global_cooldown": 60, "max_per_user_per_stream": 3})
    assert lim["is_global_cooldown_enabled"] and lim["global_cooldown_seconds"] == 60
    assert lim["is_max_per_user_per_stream_enabled"] and lim["max_per_user_per_stream"] == 3
    off = redeems._reward_limits({})
    assert not off["is_global_cooldown_enabled"]


# ── redemption handling ──────────────────────────────────────────────────────
def test_handle_redemption_fulfill_refund_backlog(monkeypatch):
    patched = []
    monkeypatch.setattr(redeems, "_fulfill", lambda rid, red, st="FULFILLED": patched.append(st))
    monkeypatch.setattr("stream_manager.chat.say", lambda t: None, raising=False)
    redeems._reward_action.clear(); redeems._reward_action["rw"] = "coinflip"
    redeems._seen.clear()

    # backlog is skipped
    monkeypatch.setitem(cfg.config, "redeems", {"auto_fulfill": True, "refund_on_failure": True})
    redeems.handle_redemption("rw", {"id": "old", "user_name": "a",
                                     "redeemed_at": "2000-01-01T00:00:00Z"})
    assert patched == []

    # success -> FULFILLED
    monkeypatch.setattr(games, "run_action", lambda a, user="", say=None, user_id="": "HEADS")
    redeems._seen.clear(); patched.clear()
    redeems.handle_redemption("rw", {"id": "n1", "user_name": "a"})
    assert patched == ["FULFILLED"]

    # failure + refund_on_failure -> CANCELED
    monkeypatch.setattr(games, "run_action", lambda a, user="", say=None, user_id="": None)
    redeems._seen.clear(); patched.clear()
    redeems.handle_redemption("rw", {"id": "n2", "user_name": "a"})
    assert patched == ["CANCELED"]


# ── automated actions (allowlist + gating) ───────────────────────────────────
def test_actions_allowlist(monkeypatch):
    monkeypatch.setitem(cfg.config, "automation", {"enabled": False})
    assert actions.run("vip", "u", "1") is False   # disabled → no-op

    monkeypatch.setitem(cfg.config, "automation", {"enabled": True, "allow_vip": True,
                                                   "allow_timeout": True, "allow_shoutout": True})
    calls = []
    monkeypatch.setattr(actions, "_helix",
                        lambda m, p, params=None, body=None: (calls.append(p) or (200, {})))
    monkeypatch.setattr("stream_manager.twitch_auth.auth", {"user_id": "9"}, raising=False)
    assert actions.run("vip", "b", "5") and calls[-1] == "channels/vips"
    assert actions.run("timeout:45", "b", "5") and calls[-1] == "moderation/bans"
    assert actions.run("mod", "b", "5") is False    # not on the allowlist


# ── eventsub routing (no socket) ─────────────────────────────────────────────
def test_eventsub_routes(monkeypatch):
    routed = []
    monkeypatch.setattr(redeems, "handle_redemption",
                        lambda rid, red, action=None: routed.append(("redeem", rid)))
    hype = []
    monkeypatch.setattr(effects, "emit",
                        lambda ch, data, summary=None: hype.append(data.get("kind")))
    eventsub._on_notification({
        "subscription": {"type": "channel.channel_points_custom_reward_redemption.add"},
        "event": {"id": "e", "reward": {"id": "rw"}, "user_name": "a", "user_id": "5"}})
    assert routed and routed[-1][1] == "rw"
    eventsub._on_notification({"subscription": {"type": "channel.raid"},
                               "event": {"from_broadcaster_user_name": "R", "viewers": 9}})
    assert "raid" in hype


# ── stats ─────────────────────────────────────────────────────────────────────
def test_stats_and_leaderboard():
    stats.record("lucky", "lucky", "alice", "VIP for a week")
    stats.record("slots", "slots", "alice", "JACKPOT")
    s = stats.summary()
    assert s["total_plays"] >= 2 and s["jackpots"] >= 1
    assert any(p["user"] == "alice" for p in s["top_players"])


# ── one-click OAuth (authorization-code flow) ────────────────────────────────
def test_authorize_url_and_redirect_port():
    twitch_auth.set_server_port(5123)
    assert twitch_auth.redirect_uri() == "http://localhost:5123/auth/callback"
    url = twitch_auth.build_authorize_url("st-ate", challenge="chal")
    assert url.startswith("https://id.twitch.tv/oauth2/authorize?")
    assert "response_type=code" in url
    assert "state=st-ate" in url
    assert "code_challenge=chal" in url and "code_challenge_method=S256" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A5123%2Fauth%2Fcallback" in url
    # every requested scope appears
    for scope in twitch_auth.SCOPES:
        assert scope.replace(":", "%3A") in url


def test_pkce_pair_is_valid_s256():
    import base64, hashlib
    verifier, challenge = twitch_auth._pkce_pair()
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == expected and "=" not in challenge


def test_callback_rejects_bad_state():
    twitch_auth._pending["state"] = "good"
    ok, msg = twitch_auth.handle_callback("code=abc&state=wrong")
    assert not ok and "match" in msg.lower()


def test_public_status_hides_tokens():
    pub = twitch_auth.public_status()
    assert "access_token" not in pub and "refresh_token" not in pub


def test_token_request_falls_back_to_pkce(monkeypatch):
    # Confidential attempt (with secret) fails, secret-free PKCE attempt succeeds.
    monkeypatch.setattr(twitch_auth, "TWITCH_CLIENT_SECRET", "sekret")
    seen = []
    def fake(url, fields):
        seen.append("client_secret" in fields)
        if "client_secret" in fields:
            return 403, {"message": "invalid client secret"}
        return 200, {"access_token": "T", "refresh_token": "R", "expires_in": 3600}
    monkeypatch.setattr(twitch_auth, "_http_form", fake)
    code, body = twitch_auth._token_request({"grant_type": "authorization_code", "code": "c"})
    assert code == 200 and body["access_token"] == "T"
    assert seen == [True, False]   # tried with secret, then without


# ── server hardening (path safety + CSRF token gating) ───────────────────────
def test_safe_join_blocks_traversal(tmp_path):
    from stream_manager import server
    root = str(tmp_path / "overlays")
    import os
    os.makedirs(root)
    open(os.path.join(root, "ok.html"), "w").close()
    assert server._safe_join("ok.html", root) is not None
    assert server._safe_join("../secret.txt", root) is None
    assert server._safe_join("..%2f..%2fetc%2fpasswd", root) is None
    # sibling-prefix must not be treated as inside
    os.makedirs(str(tmp_path / "overlays-secret"))
    open(str(tmp_path / "overlays-secret" / "x"), "w").close()
    assert server._safe_join("../overlays-secret/x", root) is None


def test_protected_posts_and_session_token():
    from stream_manager import server
    assert server.SESSION_TOKEN and len(server.SESSION_TOKEN) >= 20
    for path in ("/api/update/install", "/api/scenes/switch", "/auth/logout",
                 "/api/interactive/reload", "/api/commands/toggle",
                 "/api/commands/custom", "/api/config/save"):
        assert path in server._PROTECTED_POSTS


# ── command registry ─────────────────────────────────────────────────────────
def test_command_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setitem(cfg.config, "commands", {})
    assert commands.canonical_for("flip") == "coinflip"      # alias resolves
    assert commands.is_enabled("coinflip") is True
    commands.set_enabled("coinflip", False)
    assert commands.is_enabled("coinflip") is False
    ok, _ = commands.upsert_custom("socials", "Follow {user}", "everyone", True)
    assert ok and commands.get_custom("socials")["response"] == "Follow {user}"
    assert commands.upsert_custom("coinflip", "x")[0] is False   # built-in name rejected
    assert commands.render("hi {user} {1}", "neo", ["yo"]) == "hi neo yo"
    assert commands.delete_custom("socials") is True


def test_games_respects_disabled_and_custom(monkeypatch):
    monkeypatch.setitem(cfg.config, "commands", {
        "coinflip": {"enabled": False},
        "custom": {"hi": {"response": "yo {user}", "permission": "everyone", "enabled": True}},
    })
    sent = []
    # disabled built-in → no coinflip reply
    games.handle_command("!coinflip", "u", say=sent.append)
    assert not any("flipped" in s for s in sent)
    # custom command fires
    games.handle_command("!hi", "u", say=sent.append)
    assert "yo u" in sent
    # re-enabling restores the built-in
    monkeypatch.setitem(cfg.config, "commands", {})
    sent.clear()
    games.handle_command("!coinflip", "u", say=sent.append)
    assert any("flipped" in s for s in sent)


def test_effects_longpoll_and_global(monkeypatch):
    import threading, time
    start = effects.current_id()
    got = {}
    th = threading.Thread(target=lambda: got.update(w=effects.wait_events("wheel", start, timeout=3)))
    th.start(); time.sleep(0.1); eid = effects.emit("wheel", {"x": 1}, summary="spin"); th.join(2)
    assert got["w"]["events"] and got["w"]["events"][-1]["id"] == eid
    # global stream carries channel + summary
    s2 = effects.current_id(); g = {}
    th2 = threading.Thread(target=lambda: g.update(r=effects.wait_global(s2, timeout=3)))
    th2.start(); time.sleep(0.1); effects.emit("hype", {"kind": "raid"}, summary="RAID"); th2.join(2)
    assert g["r"]["events"][-1]["channel"] == "hype" and g["r"]["events"][-1]["summary"] == "RAID"
    # timeout returns empty promptly
    t0 = time.time(); r = effects.wait_events("none", effects.current_id(), timeout=0.3)
    assert r["events"] == [] and time.time() - t0 < 1.5


def test_validation_helpers():
    from stream_manager import server
    assert server.validate_section("command_prefix", "!")[0] is True
    assert server.validate_section("command_prefix", "!! !")[0] is False
    assert server.validate_section("cooldowns", {"coinflip": {"user": -1}})[0] is False
    assert server.validate_section("wheels", {"lucky": {"segments": [{"weight": 1}]}})[0] is False
    assert server.validate_section("wheels", {"lucky": {"segments": [{"label": "x"}]}})[0] is True
    assert server.validate_timers({"list": [{"message": ""}]})[0] is False
    assert server.validate_timers({"enabled": True, "list": [{"message": "hi", "interval": 15, "min_lines": 5}]})[0] is True


def test_alerts_first_chat_and_follow(monkeypatch):
    from stream_manager import alerts, effects
    monkeypatch.setitem(cfg.config, "alerts", {"first_chat": True, "follow_alert": True,
                                               "first_chat_message": "Hi {user}!"})
    alerts._greeted.clear(); alerts._followed.clear()
    said = []
    alerts.first_chat("Neo", say=said.append)
    alerts.first_chat("Neo", say=said.append)          # de-duped
    alerts.follow("Husky", say=said.append)
    assert said == ["Hi Neo!"]                          # follow has no message → overlay only
    texts = [e["text"] for e in effects.history("hype")]
    assert any("First chat: Neo" in t for t in texts) and any("New follower: Husky" in t for t in texts)
    # disabled → silent
    monkeypatch.setitem(cfg.config, "alerts", {"first_chat": False})
    alerts._greeted.clear(); out = []
    alerts.first_chat("X", say=out.append)
    assert out == []


def test_eventsub_follow_routing(monkeypatch):
    from stream_manager import alerts
    hits = []
    monkeypatch.setattr(alerts, "follow", lambda user, say=None: hits.append(user))
    monkeypatch.setattr("stream_manager.chat.say", lambda t: None, raising=False)
    eventsub._on_notification({"subscription": {"type": "channel.follow"},
                               "event": {"user_name": "NewFan"}})
    assert hits == ["NewFan"]


def test_song_command_and_spotify(monkeypatch):
    from stream_manager import spotify, commands
    assert commands.canonical_for("np") == "song" and commands.canonical_for("song") == "song"
    assert spotify.configured() is False and spotify.public_status()["status"] == "unconfigured"
    monkeypatch.setattr(spotify, "song_line", lambda: "🎵 Now playing: X — Y")
    sent = []
    games.handle_command("!song", "u", say=sent.append)
    assert sent and "Now playing" in sent[0]


def test_shoutout_normalise_and_guards(monkeypatch, tmp_path):
    from stream_manager import shoutout
    monkeypatch.setattr(shoutout, "LOG_FILE", str(tmp_path / "so.jsonl"))
    monkeypatch.setitem(cfg.config, "shoutout", {"chat_send": True, "mods_only": True})
    shoutout._until.clear(); shoutout._reserved.clear(); shoutout._state["enabled"] = True
    # normalisation: @name, full URL, mixed case
    assert shoutout._norm("@PixelWitch") == "pixelwitch"
    assert shoutout._norm("https://twitch.tv/PixelWitch") == "pixelwitch"
    # a successful shoutout emits a card, posts chat, and guards the repeat
    monkeypatch.setattr(shoutout, "lookup", lambda login: {
        "name": "PixelWitch", "login": "pixelwitch", "category": "Silksong",
        "live": False, "clip": "", "hold": 8000})
    said = []
    assert shoutout.do_shoutout("@PixelWitch", "command", 0, said.append) is True
    assert said and "PixelWitch" in said[0]
    assert any("Shoutout: PixelWitch" in e["text"] for e in effects.history("shoutout"))
    # immediate repeat is refused by the screen/repeat guard
    said.clear()
    assert shoutout.do_shoutout("pixelwitch", "command", 0, said.append) is False
    assert said == []


def test_shoutout_safety_and_controls(monkeypatch, tmp_path):
    from stream_manager import shoutout
    monkeypatch.setattr(shoutout, "LOG_FILE", str(tmp_path / "so.jsonl"))
    monkeypatch.setattr(shoutout, "lookup", lambda login: {"name": login, "login": login, "hold": 1000})
    shoutout._until.clear(); shoutout._reserved.clear(); shoutout._state["enabled"] = True
    # blocklist
    monkeypatch.setitem(cfg.config, "shoutout", {"blocklist": ["baduser"]})
    assert shoutout.do_shoutout("baduser") is False
    # raids under the viewer floor are ignored
    monkeypatch.setitem(cfg.config, "shoutout", {"raid_min_viewers": 5})
    assert shoutout.do_shoutout("someone", "raid", 2) is False
    # mod controls
    said = []
    shoutout.control("off", say=said.append)
    assert shoutout._state["enabled"] is False
    assert shoutout.do_shoutout("anyone") is False        # disabled
    shoutout.control("on", say=said.append)
    assert shoutout._state["enabled"] is True
    assert shoutout.control("status", say=said.append) is True
    assert shoutout.control("not-a-control") is False


def test_so_command_routing(monkeypatch):
    from stream_manager import shoutout, commands
    assert commands.canonical_for("shoutout") == "so"
    monkeypatch.setitem(cfg.config, "shoutout", {"mods_only": True})
    calls = []
    monkeypatch.setattr(shoutout, "control", lambda a, say=None: calls.append(("control", a)) or True)
    sent = []
    # non-mods are ignored entirely
    games.handle_command("!so @someone", "viewer", is_mod=False, say=sent.append)
    assert sent == [] and calls == []
    # a bare word is a control
    games.handle_command("!so skip", "mod", is_mod=True, say=sent.append)
    assert calls and calls[-1] == ("control", "skip")
    # no argument prints usage
    games.handle_command("!so", "mod", is_mod=True, say=sent.append)
    assert any("Usage:" in s for s in sent)


def test_validate_alerts():
    from stream_manager import server
    assert server.validate_section("alerts", {"first_chat": True, "first_chat_message": "hi"})[0] is True
    assert server.validate_section("alerts", {"first_chat": "yes"})[0] is False
    assert server.validate_section("alerts", {"follow_message": 5})[0] is False


def test_timers_public_list(monkeypatch):
    from stream_manager import timers
    monkeypatch.setitem(cfg.config, "timers", {"enabled": True, "list": [{"message": "hi", "interval": 10}]})
    pl = timers.public_list()
    assert pl["enabled"] is True and pl["list"][0]["message"] == "hi"


def test_config_deep_merge_and_save(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setitem(cfg.config, "cooldowns", {"coinflip": {"user": 30, "global": 3}})
    cfg.save_config({"cooldowns": {"coinflip": {"user": 99}}})
    # nested merge keeps 'global', updates 'user'
    assert cfg.config["cooldowns"]["coinflip"] == {"user": 99, "global": 3}
    import json
    on_disk = json.load(open(str(tmp_path / "config.json"), encoding="utf-8"))
    assert on_disk["cooldowns"]["coinflip"]["user"] == 99


# ── Phase 6: Stream Health Monitor ─────────────────────────────────────────
def test_health_level_thresholds():
    from stream_manager import health
    assert health._level(0.2, 1.0, 3.0) == "ok"
    assert health._level(1.5, 1.0, 3.0) == "warn"
    assert health._level(9.0, 1.0, 3.0) == "bad"
    # lower-is-worse (free disk space)
    assert health._level(50000, 10000, 2000, higher_is_worse=False) == "ok"
    assert health._level(5000, 10000, 2000, higher_is_worse=False) == "warn"
    assert health._level(500, 10000, 2000, higher_is_worse=False) == "bad"


def test_health_cfg_falls_back_to_defaults(monkeypatch):
    from stream_manager import health
    monkeypatch.setitem(cfg.config, "health", {"dropped_bad": 12.5})
    assert health.cfg("dropped_bad") == 12.5
    assert health.cfg("dropped_warn") == health.DEFAULTS["dropped_warn"]
    monkeypatch.setitem(cfg.config, "health", "not-a-dict")
    assert health.cfg("poll_sec") == health.DEFAULTS["poll_sec"]


def test_health_dropped_frames_raise_and_clear(monkeypatch, tmp_path):
    """Two OBS samples with 10% dropped frames must raise a 'bad' alert, and a
    run of healthy samples must clear it (hysteresis)."""
    from stream_manager import health
    monkeypatch.setattr(health, "LOG_FILE", str(tmp_path / "health-log.jsonl"))
    monkeypatch.setitem(cfg.config, "health", {"chat_alert": False, "clear_after": 2})
    health._prev = None
    health._alerts.clear()

    state = {"skipped": 0.0, "total": 0.0}

    def fake_fetch(mic_input="Mic/Aux"):
        return {"ok": True,
                "stats": {"activeFps": 60, "averageFrameRenderTime": 3.0,
                          "renderSkippedFrames": 0, "renderTotalFrames": state["total"],
                          "outputSkippedFrames": 0, "outputTotalFrames": state["total"],
                          "availableDiskSpace": 500000},
                "stream": {"outputActive": True, "outputCongestion": 0.05,
                           "outputSkippedFrames": state["skipped"],
                           "outputTotalFrames": state["total"],
                           "outputBytes": 0, "outputDuration": 60000}}

    monkeypatch.setattr(health.obs_ws, "fetch_stats", fake_fetch)
    monkeypatch.setattr(health, "_system_checks", lambda c, m: None)
    monkeypatch.setattr(health, "_integration_checks", lambda c, m: None)
    monkeypatch.setattr(health, "_overlay_checks", lambda c, m: None)

    health.sample()                                  # baseline, no rate yet
    state["total"] += 1000; state["skipped"] += 100  # 10% dropped this interval
    snap = health.sample()
    assert snap["metrics"]["dropped_pct"] == 10.0
    assert snap["status"] == "bad"
    assert any(a["id"] == "dropped" for a in snap["alerts"])

    for _ in range(3):                               # healthy again
        state["total"] += 1000
        snap = health.sample()
    assert snap["metrics"]["dropped_pct"] == 0.0
    assert not any(a["id"] == "dropped" for a in snap["alerts"])
    health._prev = None
    health._alerts.clear()


def test_health_preflight_skips_live_only_checks(monkeypatch):
    from stream_manager import health
    monkeypatch.setattr(health, "sample", lambda: {
        "checks": [{"id": "dropped", "label": "Dropped frames (network)",
                    "status": "bad", "message": "10%"},
                   {"id": "auth", "label": "Twitch auth", "status": "ok",
                    "message": "authorized"},
                   {"id": "obs", "label": "OBS", "status": "ok", "message": "running"}],
        "metrics": {"obs_ws": True}})
    pf = health.preflight()
    labels = [i["label"] for i in pf["items"]]
    assert "Dropped frames (network)" not in labels    # live-only, not a blocker
    assert "Twitch auth" in labels and "OBS WebSocket" in labels
    assert pf["ready"] is True


def test_health_preflight_blocks_when_obs_is_closed(monkeypatch):
    """OBS being closed is only a warning while idling, but a hard blocker
    for preflight — you cannot go live without it."""
    from stream_manager import health
    monkeypatch.setattr(health, "sample", lambda: {
        "checks": [{"id": "obs", "label": "OBS", "status": "warn",
                    "message": "not detected"}],
        "metrics": {"obs_ws": False}})
    pf = health.preflight()
    assert pf["ready"] is False
    assert "blocker" in pf["summary"]
    assert next(i for i in pf["items"] if i["label"] == "OBS")["status"] == "bad"


def test_validate_health_section():
    from stream_manager import server
    assert server.validate_section("health", {"dropped_bad": 4, "chat_alert": True})[0] is True
    assert server.validate_section("health", {"chat_template": 5})[0] is False
    assert server.validate_section("health", {"cpu_warn": -1})[0] is False
    assert server.validate_section("health", {"poll_sec": 1})[0] is False


def test_effects_heartbeat_tracking(monkeypatch):
    from stream_manager import effects
    effects.note_poll("shoutout")
    assert effects.last_poll("shoutout") is not None
    assert "shoutout" in effects.subscribers(max_age=60)
    assert "nope" not in effects.subscribers(max_age=60)


# ── v0.10.0: bitrate collapse, audio checks, session transitions ───────────
def _obs_sample(live=True, kbps=6000, muted=False, device="{hyperx}", secs=1):
    """Build the two-sample pair the rate/bitrate maths needs."""
    bytes_ = kbps * 1000 / 8 * secs
    return {"ok": True, "error": "",
            "stats": {"activeFps": 60, "averageFrameRenderTime": 3.0,
                      "renderSkippedFrames": 0, "renderTotalFrames": 60,
                      "outputSkippedFrames": 0, "outputTotalFrames": 60,
                      "availableDiskSpace": 500000},
            "stream": {"outputActive": live, "outputCongestion": 0.02,
                       "outputSkippedFrames": 0, "outputTotalFrames": 60,
                       "outputBytes": bytes_, "outputDuration": 600000},
            "audio": {"mic": {"name": "Mic/Aux", "muted": muted, "device": device},
                      "muted_inputs": (["Mic/Aux"] if muted else []),
                      "inputs": ["Mic/Aux", "Desktop Audio"]}}


def test_bitrate_collapse_is_caught_when_drops_are_zero(monkeypatch, tmp_path):
    """The 2026-09-02 failure mode: Dynamic Bitrate absorbs a bad uplink, so
    dropped frames stay at 0.0% while the picture quietly turns to mush.
    Comparing sustained bitrate against the learned target is what catches it."""
    from stream_manager import health
    monkeypatch.setattr(health, "LOG_FILE", str(tmp_path / "h.jsonl"))
    monkeypatch.setitem(cfg.config, "health", {"chat_alert": False, "bitrate_grace_sec": 0,
                                               "audio_check": False})
    health._prev = None; health._alerts.clear(); health._was_live = None
    health.reset_stream_baselines()
    monkeypatch.setattr(health, "_system_checks", lambda c, m: None)
    monkeypatch.setattr(health, "_integration_checks", lambda c, m: None)
    monkeypatch.setattr(health, "_overlay_checks", lambda c, m: None)

    rate = {"kbps": 6000}
    monkeypatch.setattr(health.obs_ws, "fetch_stats",
                        lambda mic_input="Mic/Aux": _obs_sample(kbps=rate["kbps"]))
    health.sample()                       # baseline
    rate["kbps"] = 12000                  # +6000 kbps over the interval
    snap = health.sample()
    assert snap["metrics"]["bitrate_kbps"] > 5000
    assert not any(c["id"] == "bitrate" and c["status"] != "ok" for c in snap["checks"])

    rate["kbps"] = 12050                  # only 50 kbps this interval
    snap = health.sample()
    bit = next(c for c in snap["checks"] if c["id"] == "bitrate")
    assert bit["status"] == "bad"
    assert "upload can't sustain" in bit["message"]
    # ...and crucially, dropped frames never left 0%
    dropped = next(c for c in snap["checks"] if c["id"] == "dropped")
    assert dropped["status"] == "ok"
    health._prev = None; health._alerts.clear(); health.reset_stream_baselines()


def test_muted_mic_is_bad_while_live(monkeypatch, tmp_path):
    from stream_manager import health
    monkeypatch.setattr(health, "LOG_FILE", str(tmp_path / "h.jsonl"))
    monkeypatch.setitem(cfg.config, "health", {"chat_alert": False})
    health._prev = None; health._alerts.clear(); health._was_live = None
    monkeypatch.setattr(health, "_system_checks", lambda c, m: None)
    monkeypatch.setattr(health, "_integration_checks", lambda c, m: None)
    monkeypatch.setattr(health, "_overlay_checks", lambda c, m: None)
    monkeypatch.setattr(health.obs_ws, "fetch_stats",
                        lambda mic_input="Mic/Aux": _obs_sample(muted=True))
    snap = health.sample()
    mic = next(c for c in snap["checks"] if c["id"] == "mic")
    assert mic["status"] == "bad" and "MUTED" in mic["message"]
    health._prev = None; health._alerts.clear()


def test_default_mic_device_is_flagged(monkeypatch, tmp_path):
    """Mic/Aux bound to the Windows default device switches mid-stream when a
    headset connects — exactly what the 2026-09-02 OBS log recorded."""
    from stream_manager import health
    monkeypatch.setattr(health, "LOG_FILE", str(tmp_path / "h.jsonl"))
    monkeypatch.setitem(cfg.config, "health", {"chat_alert": False})
    health._prev = None; health._alerts.clear(); health._was_live = None
    health._last_mic_device = None
    monkeypatch.setattr(health, "_system_checks", lambda c, m: None)
    monkeypatch.setattr(health, "_integration_checks", lambda c, m: None)
    monkeypatch.setattr(health, "_overlay_checks", lambda c, m: None)
    monkeypatch.setattr(health.obs_ws, "fetch_stats",
                        lambda mic_input="Mic/Aux": _obs_sample(device="default"))
    snap = health.sample()
    dev = next(c for c in snap["checks"] if c["id"] == "mic_device")
    assert dev["status"] == "warn" and "Windows default" in dev["message"]
    health._prev = None; health._alerts.clear()


def test_going_live_clears_the_welcome_list():
    """Leaving Stream Manager running across two streams used to stop first-chat
    greetings after the first one, because _greeted was never cleared."""
    from stream_manager import health, alerts
    alerts._greeted.clear()
    alerts._greeted.update({"viewer_a", "viewer_b"})
    health._was_live = False
    health._note_live(True)
    assert alerts._greeted == set()
    health._was_live = None


def test_obs_ws_error_is_actionable():
    from stream_manager import obs_ws
    import socket
    msg = obs_ws._friendly(socket.timeout("timed out"), "192.168.1.26", 4455)
    assert "127.0.0.1" in msg          # tells you how to fix a stale LAN IP
    msg = obs_ws._friendly(ConnectionRefusedError(), "127.0.0.1", 4455)
    assert "WebSocket Server Settings" in msg


def test_bitrate_target_ignores_catchup_bursts(monkeypatch, tmp_path):
    """When a stall clears, OBS flushes its buffer and one interval measures far
    above the real bitrate. That burst must not become the learned target."""
    from stream_manager import health
    monkeypatch.setattr(health, "LOG_FILE", str(tmp_path / "h.jsonl"))
    monkeypatch.setitem(cfg.config, "health", {"chat_alert": False, "bitrate_grace_sec": 0,
                                               "audio_check": False})
    health._prev = None; health._alerts.clear(); health._was_live = None
    health.reset_stream_baselines()
    monkeypatch.setattr(health, "_system_checks", lambda c, m: None)
    monkeypatch.setattr(health, "_integration_checks", lambda c, m: None)
    monkeypatch.setattr(health, "_overlay_checks", lambda c, m: None)
    rate = {"kbps": 6000}
    monkeypatch.setattr(health.obs_ws, "fetch_stats",
                        lambda mic_input="Mic/Aux": _obs_sample(kbps=rate["kbps"]))
    health.sample()
    rate["kbps"] = 12000            # a normal 6000 kbps interval
    health.sample()
    peak_before = health._peak_kbps
    rate["kbps"] = 42000            # 30000 kbps "interval" — a catch-up burst
    health.sample()
    assert health._peak_kbps == peak_before, "burst must not raise the learned target"
    health._prev = None; health._alerts.clear(); health.reset_stream_baselines()
