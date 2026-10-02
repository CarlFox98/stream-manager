"""Restart and shutdown: the flag, the endpoints, and the teardown they share.

The expensive bug here is not a broken button — it's a working one. Every extra
way out of the process is another path that can skip the save-and-close block
that used to belong to Ctrl+C alone, and the symptom (cooldowns, spin history
and session stats quietly resetting) shows up during a stream, not in a test.
"""
import io as _io
import json as _json
import os

import pytest

from stream_manager import lifecycle, server


@pytest.fixture(autouse=True)
def _clean():
    lifecycle.reset()
    yield
    lifecycle.reset()


# ── the flag ─────────────────────────────────────────────────────────────────
class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_first_request_wins():
    c = Clock()
    assert lifecycle.request("restart", clock=c) is True
    # A double-click must not turn a restart into a restart-then-quit.
    assert lifecycle.request("shutdown", clock=c) is False
    assert lifecycle.requested() == "restart"


def test_unknown_action_is_rejected():
    with pytest.raises(ValueError):
        lifecycle.request("reboot")
    assert lifecycle.requested() is None


def test_due_waits_for_the_response_to_flush():
    """ThreadingHTTPServer hands the socket to a worker and returns at once, so
    exiting the moment the flag is set truncates the reply to the click that
    caused it. The grace period is the whole point of `due` existing."""
    # The shipped value is part of what is under test — passing a grace here
    # and asserting on it would prove nothing about what actually runs.
    assert lifecycle.RESPONSE_GRACE >= 0.5
    c = Clock(1000.0)
    lifecycle.request("restart", clock=c)
    assert lifecycle.requested() == "restart"      # pending immediately…
    assert lifecycle.due(clock=c) is None          # …but not actionable yet
    c.t += lifecycle.RESPONSE_GRACE - 0.01
    assert lifecycle.due(clock=c) is None
    c.t += 0.02
    assert lifecycle.due(clock=c) == "restart"


def test_due_is_none_when_nothing_requested():
    assert lifecycle.due(clock=Clock()) is None


def test_exit_codes():
    assert lifecycle.exit_code("restart") == lifecycle.EXIT_RESTART == 42
    assert lifecycle.exit_code("shutdown") == lifecycle.EXIT_OK == 0
    assert lifecycle.exit_code(None) == lifecycle.EXIT_OK
    # 42 must not collide with the codes Python picks on its own.
    assert lifecycle.EXIT_RESTART not in (0, 1, 2)


def test_supervised_is_read_live(monkeypatch):
    monkeypatch.delenv("SM_SUPERVISED", raising=False)
    assert lifecycle.supervised() is False
    monkeypatch.setenv("SM_SUPERVISED", "1")
    assert lifecycle.supervised() is True
    monkeypatch.setenv("SM_SUPERVISED", "0")
    assert lifecycle.supervised() is False


# ── the runtime handshake file ───────────────────────────────────────────────
@pytest.fixture
def runtime(tmp_path, monkeypatch):
    path = str(tmp_path / "data" / "runtime.json")
    monkeypatch.setattr(lifecycle, "RUNTIME_FILE", path)
    return path


def test_runtime_round_trip(runtime):
    assert lifecycle.write_runtime(5000, "tok-abc") == runtime
    got = lifecycle.read_runtime()
    assert got["port"] == 5000 and got["token"] == "tok-abc"
    assert got["pid"] == os.getpid()


def test_runtime_write_leaves_no_temp_files(runtime):
    lifecycle.write_runtime(5000, "tok")
    leftovers = [n for n in os.listdir(os.path.dirname(runtime)) if n != "runtime.json"]
    assert leftovers == []


def test_a_failed_write_does_not_destroy_the_previous_file(runtime, monkeypatch):
    """Written to a temp file and renamed, not opened and overwritten.

    A direct write truncates the file first, so a disk error halfway through
    leaves an empty runtime.json — and prism-ctl then reports the app isn't
    running while it plainly is.
    """
    lifecycle.write_runtime(5000, "good")

    def boom(*a, **k):
        raise OSError("no space left on device")

    monkeypatch.setattr(lifecycle.json, "dump", boom)
    assert lifecycle.write_runtime(5001, "bad") is None

    got = lifecycle.read_runtime()
    assert got is not None and got["token"] == "good" and got["port"] == 5000
    leftovers = [n for n in os.listdir(os.path.dirname(runtime)) if n != "runtime.json"]
    assert leftovers == []          # the temp file is cleaned up on the way out


def test_runtime_clear_is_idempotent(runtime):
    lifecycle.clear_runtime()                 # never written — must not raise
    lifecycle.write_runtime(5000, "tok")
    lifecycle.clear_runtime()
    assert lifecycle.read_runtime() is None
    lifecycle.clear_runtime()


def test_runtime_rejects_unusable_files(runtime):
    os.makedirs(os.path.dirname(runtime), exist_ok=True)
    for bad in ("not json at all", '["a", "list"]', '{"port": "5000", "token": "t"}',
                '{"port": 5000}', '{"token": "t"}'):
        with open(runtime, "w", encoding="utf-8") as f:
            f.write(bad)
        assert lifecycle.read_runtime() is None, bad


# ── the endpoints ────────────────────────────────────────────────────────────
def _post(path, body=None, token=None, client="127.0.0.1"):
    """Drive Handler.do_POST over fake socket files and return (status, json)."""
    raw = b"" if body is None else _json.dumps(body).encode()

    class _Fake(server.Handler):
        def __init__(self):                  # bypass BaseHTTPRequestHandler.__init__
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


@pytest.fixture
def supervised(monkeypatch):
    monkeypatch.setenv("SM_SUPERVISED", "1")


def test_both_endpoints_require_the_session_token():
    for path in ("/api/lifecycle/restart", "/api/lifecycle/shutdown"):
        assert path in server._PROTECTED_POSTS
        status, body = _post(path, {"confirm": True}, token="wrong-token")
        assert status == 403 and body["ok"] is False
        assert lifecycle.requested() is None


def test_restart_needs_confirmation(supervised):
    for body in (None, {}, {"confirm": "yes"}, {"confirm": 1}):
        status, out = _post("/api/lifecycle/restart", body, token=server.SESSION_TOKEN)
        assert status == 400, body
        assert lifecycle.requested() is None


def test_restart_is_refused_from_another_machine(supervised):
    """--lan exists so you can watch the dashboard from the couch, not so a
    phone can stop the thing running your stream."""
    status, body = _post("/api/lifecycle/restart", {"confirm": True},
                         token=server.SESSION_TOKEN, client="192.168.1.50")
    assert status == 403
    assert "local-only" in body["error"]
    assert lifecycle.requested() is None


def test_shutdown_is_refused_from_another_machine():
    status, body = _post("/api/lifecycle/shutdown", {"confirm": True},
                         token=server.SESSION_TOKEN, client="192.168.1.50")
    assert status == 403
    assert lifecycle.requested() is None


def test_restart_is_refused_with_no_supervisor(monkeypatch):
    """Exit code 42 goes nowhere when nothing is listening for it, so Restart
    would silently be Shutdown. Say so instead."""
    monkeypatch.delenv("SM_SUPERVISED", raising=False)
    status, body = _post("/api/lifecycle/restart", {"confirm": True},
                         token=server.SESSION_TOKEN)
    assert status == 409
    assert "supervis" in body["error"].lower()
    assert lifecycle.requested() is None


def test_shutdown_works_without_a_supervisor(monkeypatch):
    monkeypatch.delenv("SM_SUPERVISED", raising=False)
    status, body = _post("/api/lifecycle/shutdown", {"confirm": True},
                         token=server.SESSION_TOKEN)
    assert status == 200 and body["ok"] is True
    assert lifecycle.requested() == "shutdown"


def test_restart_accepted_when_supervised(supervised):
    status, body = _post("/api/lifecycle/restart", {"confirm": True},
                         token=server.SESSION_TOKEN)
    assert status == 200 and body["ok"] is True and body["action"] == "restart"
    assert lifecycle.requested() == "restart"


def test_second_click_does_not_queue_a_second_action(supervised):
    _post("/api/lifecycle/restart", {"confirm": True}, token=server.SESSION_TOKEN)
    status, body = _post("/api/lifecycle/shutdown", {"confirm": True},
                         token=server.SESSION_TOKEN)
    assert status == 200 and body.get("already") is True
    assert lifecycle.requested() == "restart"


# ── the teardown every exit path shares ──────────────────────────────────────
class _FakeServer:
    def __init__(self, ):
        self.closed = False

    def server_close(self):
        self.closed = True


def _stub_teardown(monkeypatch, failing=None):
    """Record what _graceful_stop touches; `failing` raises when called."""
    from stream_manager import chat, cooldowns, eventsub, games, health, redeems, stats, timed, timers
    seen = []

    def rec(name):
        def go():
            seen.append(name)
            if name == failing:
                raise OSError("boom")
        return go

    for mod, attr, name in (
            (chat, "stop", "stop:chat"), (redeems, "stop", "stop:redeems"),
            (eventsub, "stop", "stop:eventsub"), (timers, "stop", "stop:timers"),
            (health, "stop", "stop:health"),
            (timed, "stop_loop", "stop:timed"),
            (cooldowns, "save", "save:cooldowns"),
            (games, "save_spin_history", "save:spins"),
            (stats, "flush", "save:stats")):
        monkeypatch.setattr(mod, attr, rec(name))
    return seen


def test_graceful_stop_saves_everything_and_closes(monkeypatch, runtime):
    from stream_manager import cli
    seen = _stub_teardown(monkeypatch)
    lifecycle.write_runtime(5000, "tok")

    srv = _FakeServer()
    cli._graceful_stop(srv)

    assert seen == ["stop:chat", "stop:redeems", "stop:eventsub", "stop:timers",
                    "stop:health", "stop:timed", "save:cooldowns", "save:spins", "save:stats"]
    assert srv.closed is True
    assert lifecycle.read_runtime() is None      # handshake file cleaned up


def test_loops_are_stopped_before_anything_is_saved(monkeypatch, runtime):
    """chat, redeems, eventsub and timers mutate the very state being written,
    and they are daemon threads that otherwise run right through the saves. A
    !coinflip landing in that window would be recorded into a file that had
    already been written, and lost."""
    from stream_manager import cli
    seen = _stub_teardown(monkeypatch)
    cli._graceful_stop(_FakeServer())

    last_stop = max(i for i, n in enumerate(seen) if n.startswith("stop:"))
    first_save = min(i for i, n in enumerate(seen) if n.startswith("save:"))
    assert last_stop < first_save


def test_one_failure_does_not_skip_the_rest(monkeypatch, runtime):
    """Each step is guarded on its own. Grouped under one try, a throw from
    cooldowns.save would silently take the spin history and stats with it."""
    from stream_manager import cli
    seen = _stub_teardown(monkeypatch, failing="save:cooldowns")
    srv = _FakeServer()
    cli._graceful_stop(srv)

    assert "save:spins" in seen and "save:stats" in seen
    assert srv.closed is True


def test_a_failing_stop_does_not_block_the_saves(monkeypatch, runtime):
    from stream_manager import cli
    seen = _stub_teardown(monkeypatch, failing="stop:chat")
    srv = _FakeServer()
    cli._graceful_stop(srv)

    assert seen.count("stop:redeems") == 1
    assert "save:cooldowns" in seen and "save:stats" in seen
    assert srv.closed is True


def test_the_serve_loop_tears_down_in_a_finally(monkeypatch):
    """An absence test. Only KeyboardInterrupt used to be caught, so an OSError
    out of handle_request skipped the teardown and left the port bound and
    runtime.json stale."""
    import inspect
    import re

    from stream_manager import cli
    src = inspect.getsource(cli)
    tail = src[src.index("    action = None"):]
    assert re.search(r"\n    finally:\n(?:\s*#[^\n]*\n)*\s*_graceful_stop\(server\)", tail), \
        "the teardown is not in a finally — a non-KeyboardInterrupt exit skips it"
    # and exactly one call site, so a new exit path can't quietly grow its own
    assert src.count("def _graceful_stop(server):") == 1
    assert src.count("\n        _graceful_stop(server)") == 1


def test_runtime_file_is_cleared_even_if_startup_explodes():
    """The file is written the moment the port is bound, but chat.start(),
    eventsub.start() and a first-run device login all happen after that. An
    exception in there would leave prism-ctl pointing at a process that never
    finished starting."""
    import inspect

    from stream_manager import cli
    src = inspect.getsource(cli)
    assert "atexit.register(lifecycle.clear_runtime)" in src
    i, j = src.index("lifecycle.write_runtime("), src.index("atexit.register(lifecycle.clear_runtime)")
    assert i < j, "registered before the file is written"


# ── coming back on the SAME port ─────────────────────────────────────────────
# Restart made this load-bearing. Before, a busy port at startup meant "use the
# next one" — mildly annoying, once. Now it happens mid-stream, and every OBS
# browser source is pinned to the old number.
def _leave_time_wait(host="127.0.0.1"):
    """Leave a real TIME_WAIT entry on a port and return it.

    This is the ONLY state that reproduces the bug, and the first version of
    this test missed it: a squatter that binds and closes without ever
    accepting a connection leaves nothing behind, so the test passed against
    the broken code. TIME_WAIT needs an accepted connection closed by the
    SERVER first — which is what every exit does to the dashboard's SSE stream
    and to each OBS long poll.
    """
    import socket as _socket

    listener = _socket.socket()
    listener.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    listener.bind((host, 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    client = _socket.create_connection((host, port))
    accepted, _ = listener.accept()
    accepted.close()                 # server closes first → TIME_WAIT on `port`
    client.close()
    listener.close()
    return port


def test_bind_reclaims_a_port_left_in_time_wait():
    """The bug this replaced: the old code test-bound a bare socket first, and a
    bare socket has no SO_REUSEADDR. _Server does. So the probe reported the
    port taken for the whole TIME_WAIT window (a minute on Linux, up to four on
    Windows) and every restart came back one port higher."""
    port = _leave_time_wait()
    srv, got = server.try_bind_port(port, "127.0.0.1", settle=0.0)
    try:
        assert got == port, (
            "a TIME_WAIT entry pushed the server onto another port — "
            "every OBS browser source points at the old one")
    finally:
        srv.server_close()


def test_bind_waits_briefly_for_a_port_that_is_still_held():
    """The settle window covers the other restart race: the previous process
    has not quite let go of the listening socket yet."""
    import socket as _socket
    import threading
    import time as _time

    squat = _socket.socket()
    squat.bind(("127.0.0.1", 0))
    squat.listen(1)
    port = squat.getsockname()[1]
    threading.Timer(0.4, squat.close).start()

    # Deliberately NOT passing settle: the shipped default is the thing under
    # test. An explicit value here would make this assert on its own input.
    assert server.PORT_SETTLE >= 1.0
    t0 = _time.monotonic()
    srv, got = server.try_bind_port(port, "127.0.0.1")
    try:
        assert got == port, "gave up on the configured port instead of waiting"
        assert _time.monotonic() - t0 >= 0.25, "did not actually wait"
    finally:
        srv.server_close()


def test_bind_still_walks_on_when_the_port_is_held_for_good():
    import socket as _socket

    squat = _socket.socket()
    squat.bind(("127.0.0.1", 0))
    squat.listen(1)
    port = squat.getsockname()[1]
    try:
        srv, got = server.try_bind_port(port, "127.0.0.1", settle=0.3)
        try:
            assert got != port and port < got <= port + 19
        finally:
            srv.server_close()
    finally:
        squat.close()


def test_bind_reports_the_port_it_actually_bound():
    """start=0 means "let the OS choose" — returning 0 would hand that 0 to the
    OAuth redirect URIs, runtime.json and the banner."""
    srv, got = server.try_bind_port(0, "127.0.0.1", settle=0.0)
    try:
        assert got == srv.server_address[1] != 0
    finally:
        srv.server_close()


# ── the files the feature exists to protect ──────────────────────────────────
@pytest.mark.parametrize("mod_name, file_attr, save_attr", [
    ("cooldowns", "_FILE", "save"),
    ("games", "_SPIN_FILE", "save_spin_history"),
    ("timed", "_FILE", "_save"),
    ("owed", "_FILE", "_save"),
])
def test_state_files_survive_a_write_that_dies_halfway(mod_name, file_attr, save_attr,
                                                       tmp_path, monkeypatch):
    """Truncate-then-write leaves an empty file when the process is killed mid
    save; load() then throws, the error is swallowed, and the stream starts
    with no anti-spam state. The tray can terminate a hung child, so this is a
    routine path now, not a machine-crash path."""
    import importlib
    import json as _json

    mod = importlib.import_module("stream_manager." + mod_name)
    target = str(tmp_path / "data" / (mod_name + ".json"))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    monkeypatch.setattr(mod, file_attr, target)

    with open(target, "w", encoding="utf-8") as f:
        _json.dump({"previous": 1}, f)

    def die(*a, **k):
        raise KeyboardInterrupt("killed mid-write")

    monkeypatch.setattr(mod.json, "dump", die)
    try:
        getattr(mod, save_attr)()
    except KeyboardInterrupt:
        pass                      # a kill is a kill; the file is what matters

    with open(target, encoding="utf-8") as f:
        assert _json.load(f) == {"previous": 1}, "the previous state was destroyed"


# ── the tray supervisor ──────────────────────────────────────────────────────
class _FakeProc:
    """A child that exits only when asked politely."""

    def __init__(self, answers_after=0.0):
        self.terminated = self.killed = False
        self._exited = False
        self._answers_after = answers_after
        self._born = None

    def _now(self):
        import time as _t
        if self._born is None:
            self._born = _t.monotonic()
        return _t.monotonic() - self._born

    def serving(self):
        return self._now() >= self._answers_after

    def shutdown(self):
        self._exited = True

    def poll(self):
        return 0 if self._exited else None

    def wait(self, timeout=None):
        import subprocess as _sp
        import time as _t
        deadline = _t.monotonic() + (timeout if timeout is not None else 30)
        while _t.monotonic() < deadline:
            if self._exited:
                return 0
            _t.sleep(0.02)
        raise _sp.TimeoutExpired("child", timeout)

    def terminate(self):
        self.terminated = True
        self._exited = True

    def kill(self):
        self.killed = True
        self._exited = True


def _tray_sup(proc):
    from stream_manager import tray
    sup = tray.Supervisor()
    sup.proc = proc
    sup._done.set()          # no run_forever in these tests

    def request(action):
        if not proc.serving():
            return False     # alive, but not answering yet
        proc.shutdown()
        return True

    sup._request = request
    return sup


def test_tray_quit_stops_a_healthy_child_without_terminating_it():
    proc = _FakeProc()
    _tray_sup(proc).quit(timeout=5)
    assert proc.poll() == 0
    assert proc.terminated is False and proc.killed is False


def test_tray_quit_keeps_asking_while_the_child_is_still_starting_up():
    """Startup is several seconds of OBS and Twitch calls before the first HTTP
    request is served — longer on a first-run device login. Asking once and
    reaching for terminate() killed a perfectly healthy child and took the
    unsaved cooldowns, spin history and stats with it."""
    proc = _FakeProc(answers_after=2.5)
    _tray_sup(proc).quit(timeout=10)
    assert proc.poll() == 0
    assert proc.terminated is False, "hard-killed a child that was merely still booting"


def test_tray_quit_still_terminates_a_child_that_never_answers():
    proc = _FakeProc(answers_after=10_000)
    _tray_sup(proc).quit(timeout=1.0)
    assert proc.terminated is True


def test_tray_quit_blocks_a_respawn_before_it_looks_for_the_child():
    """run_forever must see `stopping` before quit() starts probing, or it
    spawns a replacement that quit() then hard-kills."""
    import inspect

    from stream_manager import tray
    src = inspect.getsource(tray.Supervisor.quit)
    assert src.index("self.stopping = True") < src.index("self._current()")
    # and the child is re-read each pass rather than captured once
    assert src.count("self._current()") >= 2
    assert "proc = self.proc" not in src


def test_status_carries_the_supervised_flag():
    """The dashboard disables Restart from state.server.supervised. Nothing else
    tests that wiring, and the browser suite serves it from a stub."""
    from stream_manager.state import state
    assert isinstance(state["server"].get("supervised"), bool)

    import io as _io
    import json as _json

    class _Fake(server.Handler):
        def __init__(self):
            self.rfile = _io.BytesIO(b"")
            self.wfile = _io.BytesIO()
            self.client_address = ("127.0.0.1", 0)
            self.requestline = "GET /api/status HTTP/1.1"
            self.request_version = "HTTP/1.1"
            self.command = "GET"
            self.path = "/api/status"
            self.headers = {}

        def log_message(self, *a, **k):
            pass

    h = _Fake()
    h.do_GET()
    body = _json.loads(h.wfile.getvalue().decode("utf-8", "replace").partition("\r\n\r\n")[2])
    assert isinstance(body["server"]["supervised"], bool)
    # and started_at, which is how the page notices a restart it didn't ask for
    assert isinstance(body["server"]["started_at"], (int, float))
