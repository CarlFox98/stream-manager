"""Restart / shutdown plumbing shared by the server, the CLI and the tray.

Nothing in here stops the process itself. The HTTP handler records *that* a stop
was asked for; the main loop in cli.py notices and unwinds through the same
graceful path Ctrl+C has always used, and the exit code tells whatever launched
us whether to come back.
"""
import json, os, tempfile, threading, time

from .config import BASE_DIR

# Exit codes a supervisor reads. 42 has to stay clear of the ones Python picks
# on its own (0 clean, 1 unhandled exception, 2 argparse) so "come back" can
# never be confused with "died".
EXIT_OK = 0
EXIT_RESTART = 42

# How long the main loop keeps serving after a stop is requested.
#
# _Server is a ThreadingHTTPServer, so handle_request() hands the socket to a
# worker thread and returns immediately — it does NOT wait for the response to
# be written. Breaking the loop the instant the flag is set would tear the
# dashboard's own "restarting…" reply off mid-socket, and the page would show a
# network error for the click that worked.
RESPONSE_GRACE = 0.75

# Where an out-of-process caller (prism-ctl, the tray) finds the running
# instance. data/ is gitignored, same as the Twitch token cache next to it.
RUNTIME_FILE = os.path.join(BASE_DIR, "data", "runtime.json")

_lock = threading.Lock()
_action = None          # None | "restart" | "shutdown"
_deadline = 0.0

ACTIONS = ("restart", "shutdown")


def request(action, grace=RESPONSE_GRACE, clock=time.monotonic):
    """Ask the main loop to stop. Returns False if a stop was already pending.

    First request wins: a double-click on Restart must not turn into a restart
    followed by a shutdown.
    """
    global _action, _deadline
    if action not in ACTIONS:
        raise ValueError(f"unknown lifecycle action: {action!r}")
    with _lock:
        if _action is not None:
            return False
        _action = action
        _deadline = clock() + max(0.0, grace)
    return True


def requested():
    """The pending action, grace period or not. None when nothing is pending."""
    with _lock:
        return _action


def due(clock=time.monotonic):
    """The action the main loop should act on NOW, or None.

    Stays None until the grace period is up — see RESPONSE_GRACE.
    """
    with _lock:
        if _action is None or clock() < _deadline:
            return None
        return _action


def reset():
    """Clear any pending action. For tests; the process doesn't come back."""
    global _action, _deadline
    with _lock:
        _action = None
        _deadline = 0.0


def exit_code(action):
    return EXIT_RESTART if action == "restart" else EXIT_OK


def supervised():
    """True when something launched us that will relaunch on EXIT_RESTART.

    Read live rather than cached at import: the tests set the variable, and a
    cached answer would make the restart button claim a supervisor that isn't
    there — or refuse one that is.
    """
    return os.environ.get("SM_SUPERVISED") == "1"


# ── the runtime handshake file ────────────────────────────────────────────────
# A Stream Deck key runs a local process, and a local process can't hold the
# per-run session token. It reads it from here instead.
#
# This does not weaken the token. Its job is to stop a cross-site page or
# another device on the LAN from driving the dashboard's endpoints, and neither
# can read a file on this disk. Anything that CAN read it is already running as
# the user, and could restart the app by killing the process anyway.

def write_runtime(port, token, pid=None):
    """Record the running instance. Returns the path, or None if it couldn't."""
    payload = {
        "pid": os.getpid() if pid is None else pid,
        "port": port,
        "token": token,
        "started_at": time.time(),
    }
    try:
        os.makedirs(os.path.dirname(RUNTIME_FILE), exist_ok=True)
        # Write-then-rename: prism-ctl must never read a half-written file and
        # conclude the app isn't running.
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(RUNTIME_FILE),
                                   prefix=".runtime-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            try:
                # Correct on POSIX; on Windows the file inherits the folder's
                # ACL instead, so under an all-users-readable path (a drive
                # root, say) another local account could read it. It holds a
                # loopback-only session token, so the exposure is bounded —
                # but "no-op on Windows" was the wrong word for it.
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, RUNTIME_FILE)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        return None
    return RUNTIME_FILE


def clear_runtime():
    """Remove the handshake file. Safe to call when it was never written."""
    try:
        os.unlink(RUNTIME_FILE)
    except OSError:
        pass


def read_runtime():
    """The recorded instance as a dict, or None when there isn't a usable one."""
    try:
        with open(RUNTIME_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("port"), int) or not isinstance(data.get("token"), str):
        return None
    return data
