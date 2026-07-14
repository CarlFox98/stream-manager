"""
OBS status detection.

Tries the OBS WebSocket API first, which reports real streaming/recording
state and the current scene name. Falls back to tasklist/PowerShell process
detection (running + PID + process uptime only) if the WebSocket isn't
reachable or configured.
"""
import subprocess

from .obs_ws import get_obs_ws_status


def get_obs_status(state):
    if get_obs_ws_status(state):
        return
    _get_obs_status_tasklist(state)


def _get_obs_status_tasklist(state):
    # tasklist can't see streaming/recording/scene state — don't leave stale
    # values around from a WebSocket connection that has since dropped.
    state["obs"]["streaming"] = False
    state["obs"]["recording"] = False
    state["obs"]["scene"] = ""
    try:
        out = subprocess.run(["tasklist", "/fi", "imagename eq obs64.exe"],
                             capture_output=True, text=True, timeout=5).stdout
        if "obs64.exe" in out:
            pid_line = [l for l in out.splitlines() if "obs64.exe" in l]
            if pid_line:
                parts = pid_line[0].split()
                state["obs"]["pid"] = parts[1] if len(parts) > 1 else None
            state["obs"]["running"] = True
            # Get OBS process uptime via PowerShell
            try:
                script = 'try{[math]::Round(((Get-Date)-(Get-Process obs64 -ErrorAction Stop).StartTime).TotalSeconds)}catch{0}'
                r = subprocess.run(["powershell", "-noprofile", "-command", script],
                                   capture_output=True, text=True, timeout=5)
                state["obs"]["uptime"] = int(r.stdout.strip() or 0)
            except:
                state["obs"]["uptime"] = 0
        else:
            state["obs"]["running"] = False
            state["obs"]["pid"] = None
            state["obs"]["uptime"] = 0
    except:
        state["obs"]["running"] = False
        state["obs"]["uptime"] = 0
