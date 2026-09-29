"""The startup grace period in stream_manager/health.py.

At t=0 the Twitch token has not loaded and the IRC client has not finished
CAP + JOIN, so a health sample taken immediately finds both down. Reporting
those as `bad` raises real alerts — a banner, a toast, a beep, a log entry and
optionally a chat notice — on every single launch. `_update_alerts` only ever
raises on `bad`, so downgrading to `warn` during the window is what stops it.
"""
import time

import pytest

from stream_manager import health


@pytest.fixture
def clock(monkeypatch):
    """Control how long the process has 'been running'."""
    from stream_manager.state import state
    original = state["server"].get("started_at")

    def set_uptime(seconds):
        state["server"]["started_at"] = time.time() - seconds

    yield set_uptime
    if original is not None:
        state["server"]["started_at"] = original


def test_grace_window_is_active_right_after_launch(clock):
    clock(1)
    assert health._starting_up() is True


def test_grace_window_has_passed_later(clock):
    clock(health.cfg("startup_grace_sec") + 5)
    assert health._starting_up() is False


def test_grace_window_reads_the_configured_length(clock):
    clock(health.cfg("startup_grace_sec") - 2)
    assert health._starting_up() is True


def test_missing_start_time_is_not_treated_as_starting(monkeypatch):
    """A malformed state must not suppress alerts forever."""
    from stream_manager.state import state
    monkeypatch.setitem(state, "server", {})
    assert health._starting_up() is False


def _status(checks, cid):
    for c in checks:
        if c["id"] == cid:
            return c["status"], c["message"]
    return None, None


def test_a_normal_launch_does_not_raise_alerts(clock, monkeypatch):
    """The whole point: nothing `bad` while auth and chat are still coming up,
    because _update_alerts raises on `bad` alone."""
    clock(1)
    from stream_manager import chat, twitch_auth
    monkeypatch.setattr(chat, "status", {"connected": False, "error": ""}, raising=False)
    monkeypatch.setattr(twitch_auth, "auth", {"status": "unconfigured"}, raising=False)

    snap = health.sample()
    auth_status, auth_msg = _status(snap["checks"], "auth")
    chat_status, chat_msg = _status(snap["checks"], "chat")

    assert auth_status == "warn", f"auth was {auth_status!r} during the grace window"
    assert chat_status == "warn", f"chat was {chat_status!r} during the grace window"
    assert "just started" in auth_msg
    assert "just started" in chat_msg
    raised = {a["id"] for a in snap["alerts"]}
    assert "auth" not in raised and "chat" not in raised


def test_still_down_after_the_window_does_raise(clock, monkeypatch):
    """Downgrading must not become 'never alert' — a genuinely broken auth or a
    chat client that never connects still has to be reported."""
    clock(health.cfg("startup_grace_sec") + 30)
    from stream_manager import chat, twitch_auth
    monkeypatch.setattr(chat, "status", {"connected": False, "error": ""}, raising=False)
    monkeypatch.setattr(twitch_auth, "auth", {"status": "unconfigured"}, raising=False)

    snap = health.sample()
    assert _status(snap["checks"], "auth")[0] == "bad"
    assert _status(snap["checks"], "chat")[0] == "bad"
