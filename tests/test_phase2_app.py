"""App-side pieces the MCP action tools stand on.

  · lifecycle.offline_check — the restart guard. Fail-closed: anything it can't
    positively confirm as "not live" refuses.
  · /api/lifecycle/restart with offline_only — and without it, unchanged.
  · /api/shoutout/run and /api/shoutout/control — token, input vetting, reasons.
  · shoutout.run — the same gates as !so, now saying which one said no.
"""
import io as _io
import json as _json
import time

import pytest

from stream_manager import config as cfg
from stream_manager import effects, lifecycle, server, shoutout

NOW = 10_000.0


def snap(now=NOW, **over):
    """A state snapshot that is fresh and offline unless told otherwise.
    Overrides are relative to `now` where they name a time."""
    st = {"server": {"polled_at": now - 2},
          "obs": {"running": True, "ws": True, "streaming": False, "recording": False},
          "twitch": {"live": False, "checked_at": now - 3}}
    for path, value in over.items():
        sect, key = path.split("__")
        st[sect][key] = (now - (NOW - value)) if key.endswith("_at") and value else value
    return st


# ── the guard ────────────────────────────────────────────────────────────────
def test_fresh_and_offline_is_allowed():
    assert lifecycle.offline_check(snap(), now=NOW) == (True, "")


def test_obs_not_running_is_a_known_not_streaming():
    assert lifecycle.offline_check(snap(obs__running=False, obs__ws=False), now=NOW)[0] is True


def test_recording_alone_does_not_block():
    assert lifecycle.offline_check(snap(obs__recording=True), now=NOW)[0] is True


@pytest.mark.parametrize("over, words", [
    ({"obs__streaming": True}, "OBS is streaming"),
    ({"obs__ws": False}, "WebSocket"),                       # running, but can't see streaming
    ({"twitch__live": True}, "live on Twitch"),
    ({"twitch__checked_at": NOW - 600}, "Twitch hasn't confirmed"),
    ({"twitch__checked_at": 0}, "Twitch hasn't confirmed"),  # never answered (no credentials)
    ({"server__polled_at": NOW - 600}, "refreshed"),
])
def test_anything_unconfirmed_refuses(over, words):
    ok, why = lifecycle.offline_check(snap(**over), now=NOW)
    assert ok is False and words in why


def test_empty_state_refuses():
    assert lifecycle.offline_check({}, now=NOW)[0] is False


# ── endpoints ────────────────────────────────────────────────────────────────
def _post(path, body=None, token=None, client="127.0.0.1"):
    raw = b"" if body is None else _json.dumps(body).encode()

    class _Fake(server.Handler):
        def __init__(self):
            self.rfile = _io.BytesIO(raw)
            self.wfile = _io.BytesIO()
            self.client_address = (client, 0)
            self.requestline = "POST %s HTTP/1.1" % path
            self.request_version = "HTTP/1.1"
            self.command = "POST"
            self.path = path
            self.headers = {"Content-Length": str(len(raw))}
            if token is not None:
                self.headers["X-SM-Token"] = token

        def log_message(self, *a, **k):
            pass

        def log(self, *a, **k):
            pass

    h = _Fake()
    h.do_POST()
    out = h.wfile.getvalue().decode("utf-8", "replace")
    head, _, payload = out.partition("\r\n\r\n")
    return int(head.split()[1]), _json.loads(payload)


T = server.SESSION_TOKEN


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    lifecycle.reset()
    monkeypatch.setenv("SM_SUPERVISED", "1")
    yield
    lifecycle.reset()


@pytest.fixture
def live_state(monkeypatch):
    """Point the server's state at a snapshot we control."""
    def use(**over):
        st = snap(now=time.time(), **over)        # real clock: the endpoint reads it
        for sect in ("server", "obs", "twitch"):
            monkeypatch.setitem(server.state, sect, {**server.state[sect], **st[sect]})
    return use


def test_restart_offline_only_refused_while_live(live_state):
    live_state(twitch__live=True)
    status, body = _post("/api/lifecycle/restart", {"confirm": True, "offline_only": True}, token=T)
    assert status == 409 and "Not while live" in body["error"] and "Twitch" in body["error"]
    assert lifecycle.requested() is None


def test_restart_offline_only_refused_when_it_cannot_tell(live_state):
    live_state(obs__ws=False)
    status, body = _post("/api/lifecycle/restart", {"confirm": True, "offline_only": True}, token=T)
    assert status == 409 and lifecycle.requested() is None


def test_restart_offline_only_allowed_when_offline(live_state):
    live_state()
    status, body = _post("/api/lifecycle/restart", {"confirm": True, "offline_only": True}, token=T)
    assert status == 200 and body["ok"] and lifecycle.requested() == "restart"


def test_restart_without_the_flag_is_unchanged_even_while_live(live_state):
    # The dashboard, tray and Stream Deck must still be able to restart mid-stream.
    live_state(twitch__live=True, obs__streaming=True)
    status, body = _post("/api/lifecycle/restart", {"confirm": True}, token=T)
    assert status == 200 and lifecycle.requested() == "restart"


def test_offline_only_must_be_exactly_true(live_state):
    live_state(twitch__live=True)
    status, _ = _post("/api/lifecycle/restart", {"confirm": True, "offline_only": "yes"}, token=T)
    assert status == 200               # only a real true opts in; anything else is the old path


@pytest.mark.parametrize("path", ["/api/shoutout/run", "/api/shoutout/control"])
def test_shoutout_endpoints_need_the_token(path):
    status, _ = _post(path, {"login": "pixelwitch", "action": "skip"})
    assert status == 403
    status, _ = _post(path, {"login": "pixelwitch", "action": "skip"}, token="wrong")
    assert status == 403


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(shoutout, "run", lambda login, source="command", viewers=0, say=None:
                        (seen.append((login, source)) or (True, "")))
    return seen


@pytest.mark.parametrize("login", ["", "ab", "a" * 26, "pixel witch", "twitch.tv/pixelwitch",
                                   "pixelwitch;!so skip", "pixel-witch", None, 42])
def test_bad_logins_never_reach_the_shoutout(calls, login):
    status, body = _post("/api/shoutout/run", {"login": login}, token=T)
    assert status == 400 and "Twitch username" in body["error"]
    assert calls == []


def test_good_login_runs_with_its_source(calls):
    status, body = _post("/api/shoutout/run", {"login": "@PixelWitch", "source": "claude"}, token=T)
    assert status == 200 and body == {"ok": True, "login": "pixelwitch", "note": ""}
    assert calls == [("@PixelWitch", "claude")]


def test_unknown_source_falls_back_to_dashboard(calls):
    _post("/api/shoutout/run", {"login": "pixelwitch", "source": "raid"}, token=T)
    assert calls == [("pixelwitch", "dashboard")]     # can't claim to be a raid and skip gates


def test_refused_shoutout_is_409_with_the_reason(monkeypatch):
    monkeypatch.setattr(shoutout, "run", lambda *a, **k: (False, "pixelwitch was just shouted out"))
    status, body = _post("/api/shoutout/run", {"login": "pixelwitch"}, token=T)
    assert status == 409 and body["ok"] is False and "just shouted out" in body["error"]


def test_control_rejects_unknown_actions(monkeypatch):
    seen = []
    monkeypatch.setattr(shoutout, "control", lambda a, say=None: seen.append(a) or True)
    for bad in ("status", "nuke", "", None):
        status, _ = _post("/api/shoutout/control", {"action": bad}, token=T)
        assert status == 400
    assert seen == []


def test_control_runs_and_returns_status(monkeypatch):
    seen = []
    monkeypatch.setattr(shoutout, "control", lambda a, say=None: seen.append(a) or True)
    status, body = _post("/api/shoutout/control", {"action": "SKIP"}, token=T)
    assert status == 200 and seen == ["skip"] and "enabled" in body["shoutout"]


# ── shoutout.run reasons ─────────────────────────────────────────────────────
@pytest.fixture
def so(monkeypatch, tmp_path):
    monkeypatch.setattr(shoutout, "LOG_FILE", str(tmp_path / "so.jsonl"))
    monkeypatch.setattr(shoutout, "lookup", lambda login: {"name": login, "login": login, "hold": 1000})
    shoutout._until.clear(); shoutout._reserved.clear(); shoutout._state["enabled"] = True
    monkeypatch.setattr(shoutout, "_screen_free_at", 0.0)
    return monkeypatch


def test_run_success_and_bool_wrapper_agree(so):
    so.setattr(effects, "last_poll", lambda ch: 1.0)  # an overlay is listening
    assert shoutout.run("alpha") == (True, "")
    assert shoutout.do_shoutout("beta") is True


def test_run_explains_each_refusal(so):
    so.setitem(cfg.config, "shoutout", {"blocklist": ["baduser"]})
    assert "blocklist" in shoutout.run("baduser")[1]
    so.setitem(cfg.config, "shoutout", {})
    assert "own channel" in shoutout.run(cfg.TWITCH_USER)[1]
    shoutout.run("gamma")
    ok, why = shoutout.run("gamma")
    assert ok is False and "just shouted out" in why and "try again" in why
    shoutout._state["enabled"] = False
    assert shoutout.run("delta") == (False, "shoutouts are turned off")
    shoutout._state["enabled"] = True
    so.setattr(shoutout, "lookup", lambda login: None)
    assert "couldn't find" in shoutout.run("nobody_here")[1]


def test_run_warns_when_no_overlay_is_listening(so):
    so.setattr(effects, "last_poll", lambda ch: None)
    ok, note = shoutout.run("epsilon")
    assert ok is True and "won't be shown" in note


def test_run_never_raises(so):
    so.setattr(shoutout, "lookup", lambda login: 1 / 0)
    ok, why = shoutout.run("zeta")
    assert ok is False and "unexpectedly" in why


# ── regression: the overlay-theme switch ─────────────────────────────────────
def test_scene_switch_answers_instead_of_crashing(monkeypatch):
    # do_POST once assigned a local called "state", which shadowed the module's
    # state for the whole method: every /api/scenes/switch died with
    # UnboundLocalError and the dashboard's theme buttons did nothing.
    from stream_manager import scenes
    monkeypatch.setattr(scenes, "apply_scene_set", lambda name: (False, f"Unknown scene set '{name}'"))
    monkeypatch.setattr(scenes, "available_sets", lambda: [])
    monkeypatch.setattr(scenes, "detect_active_set", lambda: None)
    status, body = _post("/api/scenes/switch", {"set": "nope"}, token=T)
    assert status == 400 and "Unknown scene set" in body["message"]


def test_do_post_never_shadows_the_shared_state():
    import ast, inspect, textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(server.Handler.do_POST)))
    assigned = {t.id for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign))
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
                if isinstance(t, ast.Name)}
    assert "state" not in assigned
