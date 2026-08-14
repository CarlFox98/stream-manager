"""Logic tests for the interactive layer (games, cooldowns, redeems, actions,
stats, persistence). No network or live Twitch required.

Run from the repo root:   python -m pytest -q
"""
import random
import pytest

from stream_manager import (games, cooldowns, effects, quotes, redeems,
                             actions, stats, eventsub, twitch_auth, config as cfg)


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
    cooldowns._last_global["coinflip"] = 100.0
    cooldowns._last_user[("coinflip", "x")] = 50.0
    cooldowns.save(); cooldowns.reset(); cooldowns.load()
    assert cooldowns._last_global.get("coinflip") == 100.0
    assert cooldowns._last_user.get(("coinflip", "x")) == 50.0


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
                 "/api/interactive/reload"):
        assert path in server._PROTECTED_POSTS
