"""The MCP action tools: what they send, what they refuse, what they record.

Like test_mcp_server, the first half is stdlib-only and runs on 3.9; the MCP
protocol tests at the bottom need the SDK and skip without it.
"""
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from stream_manager import mcp_server as m

TOKEN = "tok-SECRET-never-shown-0123456789"


class FakeApp:
    """Answers /api/ping, records every POST, replies from a table of (status, body)."""

    def __init__(self, replies=None):
        table = {
            "/api/shoutout/run": (200, {"ok": True, "login": "pixelwitch", "note": ""}),
            "/api/shoutout/control": (200, {"ok": True, "action": "skip",
                                            "shoutout": {"enabled": True, "pending_raid": None,
                                                         "internal": "x"}}),
            "/api/scenes/switch": (200, {"ok": True, "active_set": "prism-haunt",
                                         "message": "Switched to prism-haunt"}),
            "/api/timed/undo-all": (200, {"ok": True, "undone": 2}),
            "/api/quotes/add": (200, {"ok": True, "quote": {"id": 7, "text": "hi", "date": "2026-10-25",
                                                             "added_by": "claude", "game": ""}}),
            "/api/lifecycle/restart": (200, {"ok": True, "action": "restart", "grace": 0.75}),
        }
        table.update(replies or {})
        posts = []

        class H(BaseHTTPRequestHandler):
            def _send(self, status, body):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path == "/api/ping":
                    self._send(200, {"app": "stream-manager", "version": "0.15.0", "pid": 1, "port": 5000})
                else:
                    self._send(404, {})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                posts.append({"path": self.path, "body": body, "token": self.headers.get("X-SM-Token")})
                self._send(*table.get(self.path, (404, {"ok": False, "error": "Not found"})))

            def log_message(self, *a):
                pass

        self.posts = posts
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def files(tmp_path, monkeypatch):
    rt, log = tmp_path / "runtime.json", tmp_path / "mcp-log.jsonl"
    monkeypatch.setattr(m, "RUNTIME_FILE", str(rt))
    monkeypatch.setattr(m, "AUDIT_LOG", str(log))
    return rt, log


@pytest.fixture
def make_app(files):
    rt, _ = files
    made = []

    def make(replies=None, token=TOKEN):
        app = FakeApp(replies)
        rt.write_text(json.dumps({"pid": 1, "port": app.port, "token": token, "started_at": 1.0}))
        made.append(app)
        return app
    yield make
    for a in made:
        a.close()


def audit_lines(files):
    _, log = files
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


# ── what each tool sends ──────────────────────────────────────────────────────
def test_shoutout_sends_login_and_source_with_the_token(make_app, files):
    app = make_app()
    out = m.shoutout(login="@PixelWitch")
    assert app.posts == [{"path": "/api/shoutout/run", "body": {"login": "@PixelWitch", "source": "claude"},
                          "token": TOKEN}]
    assert out["ok"] is True and out["login"] == "pixelwitch"


@pytest.mark.parametrize("given, wire", [("skip", "skip"), ("clear", "clear"), ("on", "on"),
                                         ("off", "off"), ("approve", "ok")])
def test_shoutout_control_maps_approve_to_ok(make_app, given, wire):
    app = make_app()
    out = m.shoutout_control(action=given)
    assert app.posts[0]["body"] == {"action": wire}
    assert "internal" not in out["shoutout"]             # allowlisted like the read tools


def test_scene_switch_undo_and_quote(make_app):
    app = make_app()
    assert m.switch_scene_set(name="prism-haunt")["active_set"] == "prism-haunt"
    assert m.undo_effects()["undone"] == 2
    q = m.add_quote(text="  a   quote\nwith  spaces ")
    assert q["quote"] == {"id": 7, "text": "hi", "date": "2026-10-25"}
    bodies = [(p["path"], p["body"]) for p in app.posts]
    assert bodies == [("/api/scenes/switch", {"set": "prism-haunt"}),
                      ("/api/timed/undo-all", {}),
                      ("/api/quotes/add", {"text": "a quote with spaces", "added_by": "claude"})]


def test_restart_always_asks_for_offline_only(make_app):
    app = make_app()
    out = m.restart()
    assert app.posts[0]["body"] == {"confirm": True, "offline_only": True}
    assert "Restart requested" in out["message"]


# ── refusals ──────────────────────────────────────────────────────────────────
def test_the_apps_reason_comes_through(make_app):
    make_app({"/api/lifecycle/restart": (409, {"ok": False, "error": "Not while live: you're live on Twitch."}),
              "/api/shoutout/run": (409, {"ok": False, "error": "pixelwitch was just shouted out"}),
              "/api/scenes/switch": (400, {"ok": False, "message": "Unknown scene set 'nope'"})})
    with pytest.raises(m.Refused, match="Not while live"):
        m.restart()
    with pytest.raises(m.Refused, match="just shouted out"):
        m.shoutout(login="pixelwitch")
    with pytest.raises(m.Refused, match="Unknown scene set"):
        m.switch_scene_set(name="nope")


@pytest.mark.parametrize("login", ["", "ab", "pixel witch", "twitch.tv/x", "a" * 26, "x;!so skip"])
def test_bad_logins_are_stopped_before_any_request(make_app, login):
    app = make_app()
    with pytest.raises(m.Refused):
        m.shoutout(login=login)
    assert app.posts == []


def test_quote_limits_are_checked_locally(make_app):
    app = make_app()
    with pytest.raises(m.Refused):
        m.add_quote(text="   ")
    with pytest.raises(m.Refused):
        m.add_quote(text="x" * (m._QUOTE_MAX + 1))
    with pytest.raises(m.Refused):
        m.shoutout_control(action="nuke")
    assert app.posts == []


def test_missing_token_is_an_error_not_an_unauthenticated_post(make_app, files):
    rt, _ = files
    app = make_app()
    rt.write_text(json.dumps({"pid": 1, "port": app.port}))
    with pytest.raises(m.ApiError, match="no session token"):
        m.undo_effects()
    assert app.posts == []


def test_not_running(files):
    with pytest.raises(m.NotRunning):
        m.restart()


# ── the audit log ─────────────────────────────────────────────────────────────
def test_every_action_is_audited_and_the_token_never_is(make_app, files):
    make_app({"/api/lifecycle/restart": (409, {"ok": False, "error": "Not while live: OBS is streaming."})})
    m.shoutout(login="pixelwitch")
    m.add_quote(text="hello")
    with pytest.raises(m.Refused):
        m.restart()
    lines = audit_lines(files)
    assert [(e["tool"], e["ok"]) for e in lines] == [("sm_shoutout", True), ("sm_add_quote", True),
                                                     ("sm_restart", False)]
    assert lines[0]["args"] == {"login": "pixelwitch"} and "OBS is streaming" in lines[2]["detail"]
    _, log = files
    assert TOKEN not in log.read_text(encoding="utf-8")


def test_long_arguments_are_clipped_in_the_log(make_app, files):
    make_app({"/api/quotes/add": (400, {"ok": False, "quote": None})})
    with pytest.raises(m.Refused):
        m.add_quote(text="y" * 290)
    assert len(audit_lines(files)[0]["args"]["text"]) < 210


def test_a_broken_log_never_breaks_a_tool(make_app, monkeypatch):
    make_app()
    monkeypatch.setattr(m, "AUDIT_LOG", "/proc/definitely/not/writable/log.jsonl")
    assert m.undo_effects()["ok"] is True


def test_errors_and_results_never_carry_the_token(make_app):
    make_app({"/api/timed/undo-all": (500, {"ok": False, "error": "boom"})})
    for fn, kw in ((m.shoutout, {"login": "pixelwitch"}), (m.undo_effects, {}), (m.restart, {})):
        try:
            out = fn(**kw)
        except (m.Refused, m.ApiError) as e:
            out = str(e)
        assert TOKEN not in json.dumps(out, default=str)


# ── sm_start ──────────────────────────────────────────────────────────────────
def test_start_when_already_running_launches_nothing(make_app, monkeypatch):
    make_app()
    monkeypatch.setattr(m, "_launch", lambda: pytest.fail("must not launch a second copy"))
    assert m.start()["already_running"] is True


def test_start_while_still_booting_launches_nothing(files, monkeypatch):
    rt, _ = files
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0)); s.listen(1)               # bound, but no /api/ping answer yet
    try:
        rt.write_text(json.dumps({"pid": 1, "port": s.getsockname()[1], "token": TOKEN}))
        monkeypatch.setattr(m, "_launch", lambda: pytest.fail("must not launch a second copy"))
        assert m.start()["starting"] is True
    finally:
        s.close()


def test_start_launches_then_waits_for_ping(files, monkeypatch):
    rt, _ = files
    launched, apps = [], []

    def fake_launch():
        launched.append(1)
        app = FakeApp()
        apps.append(app)
        rt.write_text(json.dumps({"pid": 1, "port": app.port, "token": TOKEN}))
    monkeypatch.setattr(m, "_launch", fake_launch)
    try:
        out = m.start(wait=5, poll=0.05)
    finally:
        for a in apps:
            a.close()
    assert launched == [1] and out["started"] is True and out["version"] == "0.15.0"


def test_start_reports_a_slow_start_honestly(files, monkeypatch):
    monkeypatch.setattr(m, "_launch", lambda: None)
    out = m.start(wait=0.2, poll=0.05)
    assert out["started"] is False and "isn't answering yet" in out["message"]


def test_launch_never_shares_our_stdout(monkeypatch, tmp_path):
    # Off Windows it spawns python directly; stdout is the MCP channel, so every
    # stream must be detached.
    seen = {}
    monkeypatch.setattr(m.os, "name", "posix")
    monkeypatch.setattr(m, "BASE_DIR", str(tmp_path))
    (tmp_path / "stream-manager.py").write_text("# stub")
    monkeypatch.setattr(m.subprocess, "Popen", lambda args, **kw: seen.update(kw))
    monkeypatch.setenv("SM_SUPERVISED", "1")
    m._launch()
    for stream in ("stdin", "stdout", "stderr"):
        assert seen[stream] is m.subprocess.DEVNULL
    assert "SM_SUPERVISED" not in seen["env"]


# ── the MCP surface (needs the SDK) ───────────────────────────────────────────
needs_sdk = pytest.mark.skipif(importlib.util.find_spec("mcp") is None,
                               reason="MCP SDK not installed (needs Python 3.10+)")


def _session(fn):
    import anyio
    from mcp import Client

    async def go():
        async with Client(m.build_server()) as client:
            return await fn(client)
    return anyio.run(go)


@needs_sdk
def test_fifteen_tools_and_actions_are_never_read_only():
    async def go(c):
        return (await c.list_tools()).tools
    tools = {t.name: t for t in _session(go)}
    assert len(tools) == 15
    for name, _title, _desc, _fn, params, hints in m.ACTIONS:
        t = tools[name]
        a = t.annotations
        assert a.read_only_hint is False, name
        assert a.destructive_hint is hints["destructive"] and a.idempotent_hint is hints["idempotent"], name
        assert set((t.input_schema or {}).get("properties", {})) == set(params), name
    assert tools["sm_restart"].annotations.destructive_hint is True
    enum = tools["sm_shoutout_control"].input_schema["properties"]["action"]
    assert set(enum.get("enum") or []) == {"skip", "clear", "on", "off", "approve"}


@needs_sdk
def test_actions_over_mcp(make_app):
    app = make_app({"/api/lifecycle/restart": (409, {"ok": False, "error": "Not while live: OBS is streaming."})})

    async def go(c):
        return (await c.call_tool("sm_shoutout", {"login": "pixelwitch"}),
                await c.call_tool("sm_restart", {}),
                await c.call_tool("sm_shoutout_control", {"action": "nuke"}))
    so, rs, bad = _session(go)
    assert not so.is_error and json.loads(so.content[0].text)["login"] == "pixelwitch"
    assert rs.is_error and "OBS is streaming" in rs.content[0].text
    assert bad.is_error                                     # schema rejects it before any request
    assert [p["path"] for p in app.posts] == ["/api/shoutout/run", "/api/lifecycle/restart"]
    for res in (so, rs, bad):
        assert TOKEN not in res.content[0].text
