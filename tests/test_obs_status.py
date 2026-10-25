"""OBS status: PID/uptime must keep updating while the WebSocket path is in use."""
import sys, time, types

from stream_manager import obs


def _fake_psutil(pid, started):
    class P:
        def __init__(self):
            self.pid = pid
            self.info = {"name": "obs64.exe", "create_time": started}
    m = types.ModuleType("psutil")
    m.process_iter = lambda attrs=None: iter([P()])
    m.NoSuchProcess = m.AccessDenied = Exception
    return m


def test_ws_path_refreshes_pid_and_uptime(monkeypatch):
    state = {"obs": {"running": False, "pid": "111", "uptime": 1, "scene": "", "streaming": False, "recording": False}}

    def ws_ok(st):
        st["obs"].update(running=True, scene="Gaming", streaming=False, recording=False)
        return True
    monkeypatch.setattr(obs, "get_obs_ws_status", ws_ok)
    monkeypatch.setitem(sys.modules, "psutil", _fake_psutil(5912, time.time() - 600))

    obs.get_obs_status(state)
    assert state["obs"]["pid"] == "5912"
    assert 595 <= state["obs"]["uptime"] <= 605
    assert state["obs"]["scene"] == "Gaming"        # WebSocket fields untouched
