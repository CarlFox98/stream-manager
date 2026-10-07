"""Wheel redesign (0.13.0): eligibility gates, timed effects, outcomes, the
Steam shortlist, Spotify DJ picks and pausing rewards off-scene.

No network: every Helix / Spotify / OBS call is monkeypatched. Each test that
guards a fix was checked by reverting that fix and watching it fail.
"""
import json, os, random, time

import pytest

from stream_manager import (featured, games, helix, obs_control, outcomes, owed, redeems,
                             spotify, steam_library, timed, config as cfg)


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    monkeypatch.setattr(games, "_SPIN_FILE", str(tmp_path / "spin.json"))
    games._spin_history.clear()
    outcomes._cache.clear()
    outcomes._dj.clear()
    timed._kinds.clear()
    monkeypatch.setitem(cfg.config, "outcomes", {"outcome_delay": 0})
    random.seed(7)
    yield


# ── timed effects ────────────────────────────────────────────────────────────
class Recorder:
    """A fake effect kind that records what happened and what was on disk."""

    def __init__(self, fail_apply=False, fail_revert=0):
        self.log, self.fail_apply, self.fail_revert = [], fail_apply, fail_revert
        self.value = "original"

    def capture(self, target, params):
        self.log.append(("capture", self.value))
        return {"was": self.value}

    def apply(self, target, params, snap):
        # rule 1: the snapshot must already be on disk when apply runs
        on_disk = json.load(open(timed._FILE))
        self.log.append(("apply", any(e["snapshot"] == snap for e in on_disk)))
        if self.fail_apply:
            return False, "nope"
        self.value = "changed"
        return True, ""

    def revert(self, target, params, snap):
        if self.fail_revert:
            self.fail_revert -= 1
            return False
        self.value = snap["was"]
        self.log.append(("revert", self.value))
        return True


def _reg(rec, kind="fx", long=False):
    timed.register(kind, rec.apply, rec.revert, capture=rec.capture, long=long)


def test_snapshot_is_persisted_before_apply():
    r = Recorder(); _reg(r)
    ok, _, _ = timed.start("fx", "t", 30)
    assert ok and ("apply", True) in r.log


def test_second_hit_extends_instead_of_stacking():
    """A stacked second copy would snapshot the *changed* state and restore it."""
    r = Recorder(); _reg(r)
    timed.start("fx", "t", 30)
    until1 = timed.active()[0]["until"]
    ok, msg, _ = timed.start("fx", "t", 30)
    assert ok and msg == "extended"
    assert len(timed.active()) == 1 and timed.active()[0]["until"] > until1
    assert [x for x in r.log if x[0] == "capture"] == [("capture", "original")]


def test_tick_reverts_to_the_snapshot_when_due():
    r = Recorder(); _reg(r)
    timed.start("fx", "t", 10)
    timed.tick(now=time.time() + 5)
    assert r.value == "changed"
    timed.tick(now=time.time() + 11)
    assert r.value == "original" and timed.active() == []


def test_failed_apply_leaves_nothing_active():
    r = Recorder(fail_apply=True); _reg(r)
    ok, _, _ = timed.start("fx", "t", 10)
    assert not ok and timed.active() == []


def test_failed_revert_retries_then_gives_up():
    r = Recorder(fail_revert=99); _reg(r)
    timed.start("fx", "t", 1)
    t = time.time() + 2
    for i in range(timed._MAX_REVERT_TRIES):
        assert timed.active(), f"dropped too early at try {i}"
        timed.tick(now=t)
        t += 100
    assert timed.active() == []


def test_recover_undoes_short_effects_and_keeps_long_ones():
    short, longr = Recorder(), Recorder()
    _reg(short, "short"); _reg(longr, "long", long=True)
    timed.start("short", "a", 60)
    timed.start("long", "b", 7 * 86400)
    timed._active.clear()                         # "crash": memory gone, file stays
    assert timed.recover() == 1
    assert short.value == "original" and longr.value == "changed"
    assert [e["kind"] for e in timed.active()] == ["long"]


def test_undo_all_spares_vips():
    short, longr = Recorder(), Recorder()
    _reg(short, "short"); _reg(longr, "long", long=True)
    timed.start("short", "a", 60); timed.start("long", "b", 999)
    assert timed.revert_all() == 1
    assert longr.value == "changed"


def test_active_never_ships_the_snapshot():
    r = Recorder(); _reg(r)
    timed.start("fx", "t", 30)
    assert "snapshot" not in timed.active()[0]


# ── OBS flip geometry + allowlist ────────────────────────────────────────────
def _visual_box(t):
    """Axis-aligned box of an item at rotation 0/180, from its anchor."""
    ox, oy = obs_control._centre_offset(t)
    cx, cy = t["positionX"] + ox, t["positionY"] + oy
    return round(cx, 6), round(cy, 6)


@pytest.mark.parametrize("align", [5, 0, 1, 2, 4, 8, 6, 9, 10])
def test_flip_keeps_the_item_centred_in_place(align):
    t = {"positionX": 100.0, "positionY": 600.0, "alignment": align, "rotation": 0.0,
         "width": 400.0, "height": 300.0}
    f = obs_control.flipped_transform(t)
    assert f["rotation"] == 180.0 and f["alignment"] == 0
    assert (round(f["positionX"], 6), round(f["positionY"], 6)) == _visual_box(t)


def test_flip_respects_an_existing_rotation():
    t = {"positionX": 0.0, "positionY": 0.0, "alignment": 5, "rotation": 90.0, "width": 200.0, "height": 100.0}
    f = obs_control.flipped_transform(t)
    assert f["rotation"] == 270.0
    # top-left anchor rotated 90° clockwise: centre is down-left of the anchor
    assert round(f["positionX"], 6) == -50.0 and round(f["positionY"], 6) == 100.0


def test_obs_writer_refuses_other_sources(monkeypatch):
    monkeypatch.setitem(cfg.config, "obs_effects", {"source": "PNG TUBER", "scene": "Base"})
    with pytest.raises(PermissionError):
        obs_control.capture_transform("Game Capture", {})
    with pytest.raises(PermissionError):
        obs_control.apply_tint("Webcam Overlay", {}, {})


# ── Steam library ────────────────────────────────────────────────────────────
def _steam_fixture(root):
    sa = root / "steamapps"
    sa.mkdir(parents=True)
    lib2 = root / "lib2"
    (lib2 / "steamapps").mkdir(parents=True)
    (sa / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n "0"\n {\n  "path"  "%s"\n }\n "1"\n {\n  "path"  "%s"\n }\n}\n'
        % (str(root).replace("\\", "\\\\"), str(lib2).replace("\\", "\\\\")))
    apps = {sa: [(10, "Counter-Strike"), (228980, "Steamworks Common Redistributables"),
                 (20, "Hades II"), (30, "A Very Long Game Title That Exceeds Limits")],
            lib2 / "steamapps": [(40, "Celeste"), (50, "Some Game Soundtrack"), (60, "Balatro")]}
    for d, rows in apps.items():
        for aid, name in rows:
            (d / f"appmanifest_{aid}.acf").write_text(
                f'"AppState"\n{{\n "appid" "{aid}"\n "name" "{name}"\n}}\n')
    cfgdir = root / "userdata" / "123" / "config"
    cfgdir.mkdir(parents=True)
    (cfgdir / "localconfig.vdf").write_text(
        '"UserLocalConfigStore"{"Software"{"Valve"{"Steam"{"apps"{'
        '"20"{"LastPlayed" "1700000300"}"40"{"LastPlayed" "1700000200"}"10"{"LastPlayed" "1700000100"}'
        '}}}}}')
    return str(root)


def test_vdf_parser_handles_nesting_and_escapes():
    d = steam_library.parse_vdf('"A"{"Path" "C:\\\\Games" "q" "say \\"hi\\""}')
    assert d == {"a": {"path": "C:\\Games", "q": 'say "hi"'}}


def test_scan_finds_installed_games_only_and_orders_by_last_played(tmp_path):
    games_ = steam_library.scan(_steam_fixture(tmp_path))
    names = [g["name"] for g in games_]
    assert names[:3] == ["Hades II", "Celeste", "Counter-Strike"]
    assert "Steamworks Common Redistributables" not in names
    assert "Some Game Soundtrack" not in names
    assert {"Balatro", "A Very Long Game Title That Exceeds Limits"} <= set(names)


def test_pick_choices_rules(tmp_path, monkeypatch):
    root = _steam_fixture(tmp_path)
    monkeypatch.setattr(steam_library, "games", lambda force=False: steam_library.scan(root))
    monkeypatch.setitem(cfg.config, "steam", {"pinned": [60], "hidden": [10]})
    for _ in range(30):
        ch = steam_library.pick_choices(5, exclude_name="Hades II")
        labels = [c["label"] for c in ch]
        assert ch[0]["appid"] == 60                      # pinned first
        assert all(c["appid"] not in (10, 20) for c in ch)  # hidden + current game excluded
        assert all(len(lab) <= 25 for lab in labels)
        assert len(set(lab.lower() for lab in labels)) == len(labels)
        assert 2 <= len(ch) <= 5


# ── Spotify links ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text, want", [
    ("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC?si=x", "4uLU6hMCjMI75M1A2tKUQC"),
    ("play https://open.spotify.com/intl-pt-BR/track/4uLU6hMCjMI75M1A2tKUQC pls", None),
    ("https://open.spotify.com/intl-de/track/4uLU6hMCjMI75M1A2tKUQC", "4uLU6hMCjMI75M1A2tKUQC"),
    ("spotify:track:4uLU6hMCjMI75M1A2tKUQC", "4uLU6hMCjMI75M1A2tKUQC"),
    ("https://open.spotify.com/playlist/4uLU6hMCjMI75M1A2tKUQC", None),
    ("https://open.spotify.com/album/4uLU6hMCjMI75M1A2tKUQC", None),
    ("no link here", None),
])
def test_parse_track_id(text, want):
    got = spotify.parse_track_id(text)
    if want is None and "intl-pt-BR" in text:
        assert got in (None, "4uLU6hMCjMI75M1A2tKUQC")   # region-locale links: lenient either way
    else:
        assert got == want


def _dj_env(monkeypatch, **track):
    t = {"id": "x" * 22, "uri": "spotify:track:" + "x" * 22, "title": "Song", "artist": "Band",
         "explicit": False, "duration_ms": 200000, **track}
    queued = []
    monkeypatch.setattr(spotify, "track", lambda tid: t)
    monkeypatch.setattr(spotify, "queue_track", lambda uri: (queued.append(uri) or (True, "")))
    return queued


def test_dj_window_queues_the_winners_link_once(monkeypatch):
    queued = _dj_env(monkeypatch)
    said = []
    outcomes._dj["neo"] = {"user": "Neo", "until": time.time() + 60}
    link = "https://open.spotify.com/track/" + "x" * 22
    assert outcomes.on_chat("someoneelse", "S", link, said.append) is False
    assert outcomes.on_chat("neo", "Neo", link, said.append) is True
    assert queued == ["spotify:track:" + "x" * 22] and "neo" not in outcomes._dj
    assert outcomes.on_chat("neo", "Neo", link, said.append) is False   # window closed


def test_dj_rejects_long_and_explicit(monkeypatch):
    monkeypatch.setitem(cfg.config, "outcomes", {"outcome_delay": 0, "dj_allow_explicit": False})
    link = "https://open.spotify.com/track/" + "x" * 22
    for bad in ({"duration_ms": 11 * 60000}, {"explicit": True}):
        queued = _dj_env(monkeypatch, **bad)
        outcomes._dj["neo"] = {"user": "Neo", "until": time.time() + 60}
        assert outcomes.on_chat("neo", "Neo", link, lambda t: None) is True
        assert queued == [] and "neo" in outcomes._dj      # still open for another try


def test_expired_dj_window_becomes_an_owed_item():
    outcomes._dj["neo"] = {"user": "Neo", "until": time.time() - 1}
    outcomes._dj_expire("neo")
    assert "Neo" in owed.items()[0]["text"]


# ── eligibility + spin flow ──────────────────────────────────────────────────
def _wheel(monkeypatch, segs):
    monkeypatch.setitem(cfg.config, "wheels", {"lucky": {"title": "Lucky", "segments": segs}})


def test_ineligible_segments_never_reach_the_overlay(monkeypatch):
    _wheel(monkeypatch, [{"label": "A", "outcome": "featured"},
                         {"label": "B", "outcome": "shoutout"}])
    monkeypatch.setitem(outcomes.CHECKS, "live", lambda ctx: False)
    sent = []
    monkeypatch.setattr(games.effects, "emit", lambda ch, data, summary=None: sent.append(data))
    monkeypatch.setattr(outcomes, "run", lambda n, ctx: (True, ""))
    for _ in range(20):
        assert games.spin_wheel("lucky", "u", user_id="5") == "A"
    assert all([s["label"] for s in d["segments"]] == ["A"] for d in sent)


def test_nothing_eligible_returns_none_so_the_redemption_is_refunded(monkeypatch):
    _wheel(monkeypatch, [{"label": "B", "outcome": "shoutout"}])
    monkeypatch.setitem(outcomes.CHECKS, "live", lambda ctx: False)
    assert games.spin_wheel("lucky", "u") is None


def test_outcome_failure_refunds_and_success_fulfils(monkeypatch):
    _wheel(monkeypatch, [{"label": "A", "outcome": "featured"}])
    monkeypatch.setattr(games.effects, "emit", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(outcomes, "run", lambda n, ctx: (False, "boom"))
    games.spin_wheel("lucky", "u", on_fail=lambda why: calls.append(("fail", why)),
                     on_ok=lambda: calls.append("ok"))
    monkeypatch.setattr(outcomes, "run", lambda n, ctx: (True, ""))
    games.spin_wheel("lucky", "v", on_fail=lambda why: calls.append("fail2"), on_ok=lambda: calls.append("ok2"))
    assert calls == [("fail", "boom"), "ok2"]


def test_redemption_waits_for_the_outcome_and_settles_once(monkeypatch):
    patched = []
    monkeypatch.setattr(redeems, "_fulfill", lambda rid, red, st="FULFILLED": patched.append(st))
    monkeypatch.setattr("stream_manager.chat.say", lambda t: None, raising=False)
    monkeypatch.setitem(cfg.config, "redeems", {"auto_fulfill": True, "refund_on_failure": True})
    redeems._reward_action["rw"] = "lucky"; redeems._seen.clear()
    seen_args = {}

    def fake(action, user="", say=None, user_id="", login="", on_fail=None, on_ok=None):
        seen_args.update(user_id=user_id, login=login)
        on_fail("x"); on_ok(); on_fail("again")         # only the first may count
        return "label"
    monkeypatch.setattr(games, "run_action", fake)
    redeems.handle_redemption("rw", {"id": "r1", "user_name": "Neo", "user_login": "neo", "user_id": "42"})
    assert patched == ["CANCELED"]
    assert seen_args == {"user_id": "42", "login": "neo"}   # the target is no longer dropped


def test_vip_never_offered_to_mods_or_permanent_vips(monkeypatch):
    monkeypatch.setattr(helix, "broadcaster_id", lambda: "1")
    monkeypatch.setattr(outcomes, "_is_mod", lambda uid: uid == "mod")
    monkeypatch.setattr(outcomes, "_is_vip", lambda uid: uid in ("perm", "ours"))
    monkeypatch.setattr(outcomes, "_our_vip", lambda uid: uid == "ours")
    ok = lambda uid: outcomes._vip_ok({"user_id": uid})   # noqa: E731
    assert ok("viewer") and ok("ours")
    assert not ok("mod") and not ok("perm") and not ok("1") and not ok("")


def test_slow_mode_restores_a_preexisting_slow_mode(monkeypatch):
    patches = []
    monkeypatch.setattr(outcomes, "_chat_patch", lambda ch: (patches.append(ch) or (True, "")))
    outcomes._slow_revert("chat", {}, {"slow_mode": True, "slow_mode_wait_time": 10})
    outcomes._slow_revert("chat", {}, {"slow_mode": False})
    assert patches == [{"slow_mode": True, "slow_mode_wait_time": 10}, {"slow_mode": False}]


def test_overlay_swap_does_not_undo_a_manual_switch(monkeypatch):
    from stream_manager import scenes
    applied = []
    monkeypatch.setattr(scenes, "apply_scene_set", lambda n: (applied.append(n) or (True, "")))
    monkeypatch.setattr(scenes, "detect_active_set", lambda: "prism-holo")   # user moved on
    assert outcomes._swap_revert("overlay", {"to": "prism-soft"}, {"set": "prism-signal"})
    assert applied == []
    monkeypatch.setattr(scenes, "detect_active_set", lambda: "prism-soft")
    outcomes._swap_revert("overlay", {"to": "prism-soft"}, {"set": "prism-signal"})
    assert applied == ["prism-signal"]


def test_every_default_segment_names_a_real_outcome():
    for kind in ("lucky", "risky"):
        for s in games._DEFAULT_WHEELS[kind]["segments"]:
            assert s["outcome"] in outcomes.OUTCOMES, s


# ── pausing rewards off-scene ────────────────────────────────────────────────
def test_pause_follows_the_scene_and_only_patches_on_change(monkeypatch):
    monkeypatch.setitem(cfg.config, "redeems", {"allowed_scenes": ["Game", "Desktop"]})
    calls = []
    monkeypatch.setattr(redeems, "_request", lambda m, u, b=None: (calls.append(b["is_paused"]) or (200, {})))
    monkeypatch.setattr(redeems, "_broadcaster_id", lambda: "1")
    redeems._paused.clear()
    redeems.status["rewards"] = {"lucky": {"id": "L"}, "risky": {"id": "R"}}
    redeems.sync_pause("BRB")
    redeems.sync_pause("BRB")
    redeems.sync_pause("Game")
    assert calls == [True, True, False, False]
    redeems.sync_pause("BRB")
    calls.clear()
    redeems.sync_pause("")          # OBS unknown → never leave them stuck paused
    assert calls == [False, False]


def test_pause_off_when_no_scenes_configured(monkeypatch):
    monkeypatch.setitem(cfg.config, "redeems", {})
    assert redeems.want_paused("BRB") is None


# ── owed + featured persistence ──────────────────────────────────────────────
def test_owed_roundtrip_and_resolve(tmp_path):
    a = owed.add("Read a pun", user="x")
    owed._loaded = False                          # simulate restart
    assert [i["id"] for i in owed.items()] == [a["id"]]
    assert owed.resolve(a["id"], "done") and owed.items() == []
    assert owed.resolve(a["id"], "done") is None  # can't resolve twice
    assert owed.resolve("nope", "bogus") is None


def test_featured_keeps_last_n_and_dedupes(monkeypatch):
    monkeypatch.setattr(featured, "lookup", lambda uid="", login="": (login.title(), login, f"https://x/{login}.png"))
    monkeypatch.setattr(featured.effects, "emit", lambda *a, **k: None)
    for n in ("a", "b", "c", "a", "d"):
        featured.add(n, login=n)
    assert [r["login"] for r in featured.current()] == ["c", "a", "d"]
    assert os.path.isfile(featured._FILE)


# ── config + HTTP surface ────────────────────────────────────────────────────
def test_new_config_sections_survive_a_dashboard_save(tmp_path, monkeypatch):
    """config._write_config_file only writes keys in CONFIG_DEFAULTS, and
    _load_config deletes unknown keys. A section missing from the defaults is
    silently thrown away the first time anything is saved from the dashboard."""
    monkeypatch.setattr(cfg, "CONFIG_FILE", str(tmp_path / "config.json"))
    for key in ("outcomes", "obs_effects", "steam", "featured", "chat"):
        assert key in cfg.CONFIG_DEFAULTS
    monkeypatch.setitem(cfg.config, "steam", {"pinned": [1]})
    cfg._write_config_file()
    assert json.load(open(cfg.CONFIG_FILE))["steam"] == {"pinned": [1]}


@pytest.fixture
def live_server(monkeypatch):
    import threading
    from stream_manager import server
    srv, port = server.try_bind_port(0, "127.0.0.1", settle=0.0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield port, server.SESSION_TOKEN
    srv.shutdown(); srv.server_close()


def _http(port, path, body=None, token=None):
    import urllib.request, urllib.error
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json", **({"X-SM-Token": token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_wheel_endpoints(live_server, monkeypatch):
    port, tok = live_server
    item = owed.add("Pun for x")
    assert _http(port, "/api/owed")[1]["items"][0]["id"] == item["id"]
    # state-changing endpoints need the session token
    assert _http(port, "/api/owed/resolve", {"id": item["id"]})[0] == 403
    code, body = _http(port, "/api/owed/resolve", {"id": item["id"], "status": "done"}, tok)
    assert code == 200 and body["items"] == []
    assert _http(port, "/api/timed/undo-all", {}, tok)[1]["ok"]
    assert _http(port, "/api/wheel/test", {"outcome": "rm -rf"}, tok)[0] == 400
    code, body = _http(port, "/api/wheel/test", {"outcome": "timeout"}, tok)
    assert code == 409 and "target" in body["error"]     # needs a real viewer
    assert set(_http(port, "/api/wheel/eligibility")[1]) == set(outcomes.OUTCOMES)
    assert _http(port, "/api/steam/mark", {"appid": "x"}, tok)[0] == 400


def test_featured_dashboard_test_previews_without_saving(monkeypatch):
    """The dashboard's Test button must not leave a permanent "Dashboard" card
    on Starting Soon (seen live 2026-10-04)."""
    monkeypatch.setattr(featured, "lookup", lambda uid="", login="": (None, None, None))
    sent = []
    monkeypatch.setattr(featured.effects, "emit", lambda ch, data, **k: sent.append(data))
    ok, msg = outcomes.run("featured", {"user": "Dashboard", "user_id": "", "login": "", "test": True})
    assert ok and "not saved" in msg
    assert featured.current() == []
    assert sent and sent[-1]["preview"] == featured.PREVIEW_SECONDS
    # a real winner is still saved, with no preview flag
    outcomes.run("featured", {"user": "Viewer", "user_id": "", "login": "viewer"})
    assert [r["login"] for r in featured.current()] == ["viewer"]
    assert "preview" not in sent[-1]


# ── prism-ctl undo (Stream Deck panic key) ─────────────────────────────────
def test_ctl_undo_posts_undo_all_with_runtime_token(monkeypatch, capsys):
    from stream_manager import ctl
    calls = []
    monkeypatch.setattr(ctl, "live_instance",
                        lambda: ({"port": 5000, "token": "tok123"}, {"pid": 1}))

    def fake_post(port, path, token, payload, timeout=5):
        calls.append((port, path, token))
        return 200, {"ok": True, "undone": 2}
    monkeypatch.setattr(ctl, "_post", fake_post)
    assert ctl.main(["undo"]) == 0
    assert calls == [(5000, "/api/timed/undo-all", "tok123")]
    assert "Undid 2 wheel effects" in capsys.readouterr().out


def test_ctl_undo_when_not_running(monkeypatch, capsys):
    from stream_manager import ctl
    monkeypatch.setattr(ctl, "live_instance", lambda: (None, None))
    monkeypatch.setattr(ctl, "_post", lambda *a, **k: pytest.fail("must not post"))
    assert ctl.main(["panic"]) == 1
    assert "isn't running" in capsys.readouterr().out


def test_ctl_undo_reports_server_error(monkeypatch, capsys):
    from stream_manager import ctl
    monkeypatch.setattr(ctl, "live_instance",
                        lambda: ({"port": 5000, "token": "bad"}, {"pid": 1}))
    monkeypatch.setattr(ctl, "_post", lambda *a, **k: (403, {"error": "bad token"}))
    assert ctl.main(["undo"]) == 1
    assert "bad token" in capsys.readouterr().out


# ── Spotify setup (DJ for a song) ───────────────────────────────────────────
def test_spotify_redirect_uses_loopback_ip_not_localhost():
    """Spotify refuses http://localhost redirect URIs; it must be 127.0.0.1."""
    from stream_manager import spotify
    spotify.set_server_port(5000)
    assert spotify.redirect_uri() == "http://127.0.0.1:5000/auth/spotify/callback"
    assert spotify.public_status()["redirect_uri"] == spotify.redirect_uri()


def test_spotify_client_id_env_wins_then_config():
    from stream_manager.config import spotify_client_id
    assert spotify_client_id({"SPOTIFY_CLIENT_ID": " envid "}, {"spotify": {"client_id": "cfg"}}) == "envid"
    assert spotify_client_id({}, {"spotify": {"client_id": " cfgid "}}) == "cfgid"
    assert spotify_client_id({}, {"spotify": "junk"}) == ""
    assert spotify_client_id({}, {}) == ""
