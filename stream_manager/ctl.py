"""prism-ctl — drive a running Stream Manager from another local process.

This exists for the Stream Deck key. A key press runs a program; a program can't
hold the dashboard's per-run session token, so it reads it out of
data/runtime.json (see lifecycle.write_runtime for why that's safe) and talks to
the same endpoints the dashboard buttons use.

Deliberately light on imports: no server, no state, nothing that starts a
thread. A Stream Deck key should feel instant, and importing the app would boot
a second poll loop just to send one request.

    python -m stream_manager.ctl status
    python -m stream_manager.ctl restart
    python -m stream_manager.ctl stop
    python -m stream_manager.ctl start
    python -m stream_manager.ctl undo      # undo every active wheel effect
"""
import json, os, socket, subprocess, sys, urllib.error, urllib.request

from . import __version__
from .config import BASE_DIR
from .lifecycle import read_runtime

LAUNCHER = os.path.join(BASE_DIR, "Start Stream Manager.bat")
_TIMEOUT = 5


def _get(port, path, timeout=_TIMEOUT):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(port, path, token, payload, timeout=_TIMEOUT):
    """(status, body). Returns the body even on 4xx — that's where the reason is."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-SM-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def live_instance():
    """The running instance as (info, ping), or (None, None).

    runtime.json alone isn't proof: a crash leaves it behind. /api/ping is what
    actually settles whether anything is listening.
    """
    info = read_runtime()
    if not info:
        return None, None
    try:
        ping = _get(info["port"], "/api/ping", timeout=1.5)
    except Exception:
        return None, None
    if not isinstance(ping, dict) or ping.get("app") != "stream-manager":
        return None, None
    return info, ping


def _port_busy(port, timeout=0.4):
    """Is anything listening there at all?

    live_instance() needs an /api/ping answer, and there are seconds where the
    app is bound but not yet serving — the whole startup sequence runs after
    the port is taken, and longer still on a first-run device login. Judging
    "not running" from that would have `prism-ctl start` launch a second
    instance, and two instances fight over OBS, chat and the log files.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _launch():
    """Start a supervised instance detached from this process."""
    if os.name == "nt" and os.path.isfile(LAUNCHER):
        os.startfile(LAUNCHER)          # noqa: S606 — a file we ship, in our own folder
        return True
    entry = os.path.join(BASE_DIR, "stream-manager.py")
    if not os.path.isfile(entry):
        return False
    env = dict(os.environ)
    env.pop("SM_SUPERVISED", None)      # a bare spawn has no supervisor; don't claim one
    subprocess.Popen([sys.executable, entry], cwd=BASE_DIR, env=env)
    return True


def _act(action):
    info, ping = live_instance()
    if not info:
        print("Stream Manager isn't running.")
        return 1
    status, body = _post(info["port"], f"/api/lifecycle/{action}", info["token"],
                         {"confirm": True})
    if status == 200 and body.get("ok"):
        if body.get("already"):
            print(f"Already {body.get('action', action)}ing.")
        else:
            print(f"{action.capitalize()} requested (PID {ping.get('pid', '?')}).")
        return 0
    print(body.get("error") or f"{action} failed (HTTP {status}).")
    return 1


def cmd_undo():
    """Panic button: revert every short wheel effect (flip, tint, emote-only,
    slow mode, overlay swap). Long ones like a week of VIP are left alone —
    revoke those from the dashboard's Wheel tab."""
    info, _ = live_instance()
    if not info:
        print("Stream Manager isn't running.")
        return 1
    status, body = _post(info["port"], "/api/timed/undo-all", info["token"], {})
    if status == 200 and body.get("ok"):
        n = body.get("undone", 0)
        print(f"Undid {n} wheel effect{'' if n == 1 else 's'}.")
        return 0
    print(body.get("error") or f"undo failed (HTTP {status}).")
    return 1


def cmd_status():
    info, ping = live_instance()
    if not info:
        print("Stream Manager isn't running.")
        return 1
    print(f"Stream Manager v{ping.get('version', '?')} — "
          f"PID {ping.get('pid', '?')} on http://localhost:{info['port']}")
    return 0


def cmd_start():
    info, _ = live_instance()
    if info:
        print(f"Already running on http://localhost:{info['port']}.")
        return 0
    stale = read_runtime()
    if stale and _port_busy(stale.get("port")):
        print("Stream Manager is starting up — give it a moment.")
        return 0
    if not _launch():
        print("Couldn't find Start Stream Manager.bat or stream-manager.py.")
        return 1
    print("Starting Stream Manager…")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = (argv[0] if argv else "status").lower().lstrip("-")
    if cmd in ("version", "v"):
        print(__version__); return 0
    if cmd == "status":
        return cmd_status()
    if cmd == "start":
        return cmd_start()
    if cmd == "restart":
        return _act("restart")
    if cmd in ("stop", "shutdown", "quit"):
        return _act("shutdown")
    if cmd in ("undo", "undo-all", "panic"):
        return cmd_undo()
    print(f"Usage: prism-ctl [status|start|restart|stop|undo]\nUnknown command: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
