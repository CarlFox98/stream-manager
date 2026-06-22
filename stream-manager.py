#!/usr/bin/env python3
"""
Stream Manager — web dashboard + overlay server + system monitor
"""
__version__ = "0.1.0"

import argparse, json, os, socket, subprocess, sys, time, threading, urllib.parse, urllib.request, urllib.error, webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# ── Terminal styling ──────────────────────────────────────────
def _init_ansi():
    if os.name == "nt":
        os.system("")                              # enable VT processing
        os.system("chcp 65001 >nul 2>&1")          # UTF-8 codepage
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass  # Python < 3.7
_init_ansi()

S = {
    "R": "\033[0m", "B": "\033[1m", "D": "\033[90m",
    "G": "\033[92m", "Y": "\033[93m", "C": "\033[96m",
    "M": "\033[95m", "W": "\033[97m",
}

def style(tag, text=""):
    return f"{S.get(tag, '')}{text}{S['R']}"

def icon(ok): return f"{style('G', '●')}" if ok else f"{style('R', '○')}"

# ── File logging ────────────────────────────────────────────────────
_log_file_path = None

def setup_file_logging(rel_path):
    global _log_file_path
    path = os.path.expandvars(rel_path)
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    _log_file_path = path
    if os.path.isfile(path) and os.path.getsize(path) > 1048576:
        try:
            os.rename(path, path + ".old")
        except: pass

def write_file_log(plain):
    if _log_file_path:
        try:
            with open(_log_file_path, "a", encoding="utf-8") as _f:
                _f.write(plain + "\n")
        except: pass

MIME_MAP = {
    ".html": "text/html", ".css": "text/css", ".js": "application/javascript",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml", ".json": "application/json",
}

# ── Config ────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

CONFIG_DEFAULTS = {
    "port": 5000,
    "poll_interval": 5,
    "twitch_user": "NeoTheFox98",
    "assets_dir": r"%USERPROFILE%\Pictures\OBS Assets",
    "log_file": "server.log",
}

config = dict(CONFIG_DEFAULTS)
if os.path.isfile(CONFIG_FILE):
    try:
        with open(CONFIG_FILE) as _f:
            config.update(json.load(_f))
    except Exception as _e:
        print(f"[config] Failed to load config.json: {_e}")

# Validate config
for _key, _typ in [("poll_interval", (int, float)), ("port", int)]:
    if not isinstance(config.get(_key), _typ):
        print(f"[config] {_key} must be {_typ}, got {type(config.get(_key)).__name__}, using default {CONFIG_DEFAULTS[_key]}")
        config[_key] = CONFIG_DEFAULTS[_key]
for _key, _val in list(config.items()):
    if _key not in CONFIG_DEFAULTS:
        print(f"[config] Unknown key '{_key}' in config.json, ignoring")
        del config[_key]
for _key in CONFIG_DEFAULTS:
    if _key not in config:
        print(f"[config] Missing key '{_key}' in config.json, using default: {CONFIG_DEFAULTS[_key]}")
        config[_key] = CONFIG_DEFAULTS[_key]

ASSETS_DIR = os.path.normpath(os.path.realpath(os.path.expandvars(config["assets_dir"])))
OVERLAYS_DIR = os.path.join(ASSETS_DIR, "overlays")
TWITCH_USER = config["twitch_user"]

# ── config.env / env vars ────────────────────────────────────────────
_load_env = os.path.join(BASE_DIR, ".env")
if os.path.isfile(_load_env):
    for _line in open(_load_env):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            k, v = _line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

TWITCH_CLIENT_ID = os.environ.get("TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "")

# ── State ─────────────────────────────────────────────────────────────
state = {
    "obs": {"running": False, "pid": None, "uptime": 0},
    "twitch": {"live": False, "title": "", "game": "", "viewers": 0, "started_at": "", "uptime": "", "connected": False,
               "display_name": "", "profile_image_url": "", "view_count": 0},
    "system": {"cpu": 0, "ram_pct": 0, "ram_used_gb": 0, "ram_total_gb": 0, "gpu": ""},
    "server": {"started_at": time.time(), "uptime": "", "port": 5000},
    "requests": []
}

def get_obs_status():
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

def get_system_stats():
    try:
        import psutil
        state["system"]["cpu"] = round(psutil.cpu_percent(interval=0.5), 1)
        mem = psutil.virtual_memory()
        state["system"]["ram_pct"] = round(mem.percent, 1)
        state["system"]["ram_used_gb"] = round(mem.used / (1024**3), 1)
        state["system"]["ram_total_gb"] = round(mem.total / (1024**3), 1)
    except ImportError:
        # Fallback: PowerShell CIM/WMI
        try:
            out = subprocess.run(
                ["powershell", "-noprofile", "-command",
                 "Get-CimInstance Win32_Processor | Select-Object -ExpandProperty LoadPercentage"],
                capture_output=True, text=True, timeout=5)
            cpu_str = out.stdout.strip()
            if cpu_str.isdigit():
                state["system"]["cpu"] = int(cpu_str)
        except: pass
        try:
            script = (
                "$os = Get-CimInstance Win32_OperatingSystem; "
                "$total = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1); "
                "$free  = [math]::Round($os.FreePhysicalMemory / 1MB, 1); "
                "$used  = $total - $free; "
                "$pct   = [math]::Round(($used / $total) * 100, 1); "
                "Write-Output \"$total|$used|$pct\""
            )
            out = subprocess.run(
                ["powershell", "-noprofile", "-command", script],
                capture_output=True, text=True, timeout=5)
            parts = out.stdout.strip().split("|")
            if len(parts) == 3:
                state["system"]["ram_total_gb"] = float(parts[0])
                state["system"]["ram_used_gb"] = float(parts[1])
                state["system"]["ram_pct"] = float(parts[2])
        except: pass

def get_gpu_stats():
    try:
        out = subprocess.run(
            ["powershell", "-noprofile", "-command",
             "Get-CimInstance Win32_VideoController | "
             "Where-Object { $_.Name -notlike '*Virtual*' -and $_.Name -notlike '*Remote*' -and $_.Name -notlike '*Basic*' } | "
             "Select-Object -First 1 | Select-Object -ExpandProperty Name"],
            capture_output=True, text=True, timeout=5)
        name = out.stdout.strip()
        if not name:
            # fallback: first controller
            out = subprocess.run(
                ["powershell", "-noprofile", "-command",
                 "Get-CimInstance Win32_VideoController | Select-Object -First 1 | Select-Object -ExpandProperty Name"],
                capture_output=True, text=True, timeout=5)
            name = out.stdout.strip()
        if name:
            state["system"]["gpu"] = name
    except: pass

# ── Twitch OAuth & API ──────────────────────────────────────────────────
_twitch_token = {"access_token": None, "expires_at": 0}

def _twitch_oauth():
    """Get a valid app access token (client credentials flow)."""
    now = time.time()
    if _twitch_token["access_token"] and now < _twitch_token["expires_at"] - 60:
        return _twitch_token["access_token"]
    if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
        return None
    data = urllib.parse.urlencode({
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "grant_type": "client_credentials",
    }).encode()
    try:
        req = urllib.request.Request(
            "https://id.twitch.tv/oauth2/token", data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read())
            _twitch_token["access_token"] = body["access_token"]
            _twitch_token["expires_at"] = now + body["expires_in"]
            return _twitch_token["access_token"]
    except Exception as e:
        print(f"[twitch] OAuth error: {e}")
        return None

def get_twitch_status():
    token = _twitch_oauth()
    if not token:
        state["twitch"]["connected"] = False
        state["twitch"].update({"live": False, "title": "", "game": "", "viewers": 0, "started_at": "", "uptime": ""})
        return
    state["twitch"]["connected"] = True
    try:
        url = f"https://api.twitch.tv/helix/streams?user_login={TWITCH_USER}"
        req = urllib.request.Request(url, headers={
            "Client-ID": TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {token}",
        })
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            if data.get("data"):
                s = data["data"][0]
                state["twitch"]["live"] = True
                state["twitch"]["title"] = s.get("title", "")
                state["twitch"]["game"] = s.get("game_name", "")
                state["twitch"]["viewers"] = s.get("viewer_count", 0)
                state["twitch"]["started_at"] = s.get("started_at", "")
                if state["twitch"]["started_at"]:
                    started = datetime.fromisoformat(state["twitch"]["started_at"].replace("Z", "+00:00"))
                    delta = datetime.now().astimezone() - started
                    h, r = divmod(int(delta.total_seconds()), 3600)
                    m, s_ = divmod(r, 60)
                    state["twitch"]["uptime"] = f"{h}h {m}m" if h else f"{m}m {s_}s"
                else:
                    state["twitch"]["uptime"] = ""
            else:
                state["twitch"]["live"] = False
                state["twitch"]["title"] = ""
                state["twitch"]["game"] = ""
                state["twitch"]["viewers"] = 0
                state["twitch"]["started_at"] = ""
                state["twitch"]["uptime"] = ""
    except urllib.error.HTTPError as e:
        print(f"[twitch] API error {e.code}: {e.read().decode()}")
        if e.code in (401, 403):
            _twitch_token["access_token"] = None  # force re-auth
    except Exception as e:
        print(f"[twitch] Error: {e}")

def get_twitch_user_info():
    token = _twitch_oauth()
    if not token: return
    try:
        url = f"https://api.twitch.tv/helix/users?login={TWITCH_USER}"
        req = urllib.request.Request(url, headers={
            "Client-ID": TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {token}",
        })
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            if data.get("data"):
                u = data["data"][0]
                state["twitch"]["display_name"] = u.get("display_name", "")
                state["twitch"]["profile_image_url"] = u.get("profile_image_url", "")
                state["twitch"]["view_count"] = u.get("view_count", 0)
    except: pass

def compute_uptime(ts):
    if not ts: return ""
    delta = int(time.time() - ts)
    if delta < 60: return f"{delta}s"
    h, r = divmod(delta, 3600); m, s = divmod(r, 60)
    return f"{h}h {m}m" if h else f"{m}m {s}s"

def poll_loop():
    while True:
        get_obs_status()
        get_system_stats()
        get_twitch_status()
        get_twitch_user_info()
        state["server"]["uptime"] = compute_uptime(state["server"]["started_at"])
        time.sleep(config["poll_interval"])

threading.Thread(target=poll_loop, daemon=True).start()

# ── HTTP Handler ──────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(302); self.send_header("Location", "/dashboard")
            self.end_headers(); return

        if self.path == "/dashboard":
            self.serve_dashboard(); return

        if self.path == "/api/status":
            self.serve_json(state); return

        if self.path == "/api/health":
            self.serve_json({
                "status": "ok", "port": state["server"]["port"],
                "uptime": state["server"]["uptime"],
            }); return

        # Serve overlay / asset files (path-traversal safe)
        if self.path.startswith("/overlays/"):
            rel = self.path.lstrip("/")
            filepath = os.path.normpath(os.path.realpath(os.path.join(ASSETS_DIR, rel)))
            if os.path.isfile(filepath) and filepath.startswith(ASSETS_DIR):
                ext = os.path.splitext(filepath)[1].lower()
                mime = MIME_MAP.get(ext, "application/octet-stream")
                self.serve_file(filepath, mime)
                self.log(f"Served: {rel}", "→")
                return

        self.send_response(404); self.end_headers()
        self.wfile.write(b"Not found")

    def serve_dashboard(self):
        html = DASHBOARD_HTML
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(html.encode()))
        self.end_headers()
        self.wfile.write(html.encode())
        self.log("Served dashboard", "→")

    def serve_json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, path, mime):
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def log(self, msg, kind="~"):
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = {"~": style("D", "~"), "✓": style("G", "✓"), "✗": style("R", "✗"),
                  "→": style("C", "→"), "!": style("Y", "!")}.get(kind, style("D", "~"))
        plain = f"[{ts}] {msg}"
        colored = f"{style('D', f'[{ts}]')} {prefix} {msg}"
        state["requests"].insert(0, plain)
        if len(state["requests"]) > 100:
            state["requests"] = state["requests"][:100]
        print(colored)
        write_file_log(plain)

    def log_message(self, format, *args):
        pass  # suppress default logging

# ── Dashboard HTML ────────────────────────────────────────────────────
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stream Manager</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap');
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Inter', sans-serif; background: #0d0016; color: #e0d6f5;
  display: flex; min-height: 100vh; padding: 20px; gap: 20px;
}
/* ── Sidebar ── */
.sidebar {
  width: 260px; flex-shrink: 0;
  background: rgba(13,0,22,0.9); border: 1px solid rgba(124,58,237,0.2);
  border-radius: 16px; padding: 24px; display: flex; flex-direction: column; gap: 8px;
  height: fit-content; position: sticky; top: 20px;
}
.sidebar h1 { font-size: 18px; font-weight: 800; color: #a78bfa; margin-bottom: 4px; }
.sidebar .sub { font-size: 11px; color: #6d5a8a; margin-bottom: 12px; }
.sidebar .nav-item {
  padding: 10px 14px; border-radius: 10px; font-size: 13px; font-weight: 600;
  color: #8b7aa8; cursor: default; transition: all 0.2s;
}
.sidebar .nav-item.active {
  background: rgba(124,58,237,0.15); color: #c4b5fd;
}
/* ── Main ── */
.main { flex: 1; display: flex; flex-direction: column; gap: 20px; max-width: 900px; }
.row { display: flex; gap: 16px; flex-wrap: wrap; }
.card {
  background: rgba(13,0,22,0.85); border: 1px solid rgba(124,58,237,0.15);
  border-radius: 14px; padding: 20px; flex: 1; min-width: 200px;
  position: relative; overflow: hidden;
  transition: border-color 0.3s, box-shadow 0.3s;
}
.card:hover {
  border-color: rgba(124,58,237,0.4);
  box-shadow: 0 4px 24px rgba(124,58,237,0.08);
}
.card::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
  background: linear-gradient(90deg, #7c3aed, #a78bfa);
}
.card.card-live::before { background: linear-gradient(90deg, #22c55e, #4ade80); }
.card h3 { font-size: 11px; font-weight: 700; color: #6d5a8a; text-transform: uppercase;
  letter-spacing: 1.5px; margin-bottom: 10px; }
.status-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%;
  margin-right: 8px; animation: pulse 2s infinite; }
.status-dot.on { background: #22c55e; box-shadow: 0 0 12px rgba(34,197,94,0.4); }
.status-dot.off { background: #ef4444; box-shadow: 0 0 12px rgba(239,68,68,0.3); }
.status-dot.warn { background: #eab308; box-shadow: 0 0 12px rgba(234,179,8,0.3); }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }
.stat-value { font-size: 28px; font-weight: 800; margin-top: 4px; }
.stat-value .unit { font-size: 14px; font-weight: 400; color: #6d5a8a; }
.stat-label { font-size: 12px; color: #8b7aa8; margin-top: 2px; }
.bar-bg { height: 6px; background: rgba(124,58,237,0.1); border-radius: 3px;
  margin-top: 10px; overflow: hidden; }
.avatar { width: 48px; height: 48px; border-radius: 50%; border: 2px solid rgba(124,58,237,0.4); margin-bottom: 8px; display: block; }
.clock { font-size: 20px; font-weight: 800; color: #c4b5fd; margin-bottom: 2px; }
.clock-date { font-size: 10px; color: #6d5a8a; margin-bottom: 8px; }
.bar-fill { height: 100%; border-radius: 3px; transition: width 0.5s;
  background: linear-gradient(90deg, #7c3aed, #ec4899); }
.twitch-title { font-size: 13px; color: #f9f5ff; font-weight: 600;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.twitch-game { font-size: 11px; color: #8b7aa8; margin-top: 2px; }
/* ── Log ── */
.log-card { flex: none; }
.log-box {
  background: rgba(0,0,0,0.3); border-radius: 8px; padding: 12px; height: 200px;
  overflow-y: auto; font-family: 'Courier New', monospace; font-size: 11px;
  line-height: 1.6;
}
.log-timestamp { color: #6d5a8a; }
.log-badge { display: inline-block; width: 14px; text-align: center; margin-right: 4px; font-size: 10px; }
.log-badge.ok { color: #22c55e; }
.log-badge.err { color: #ef4444; }
.log-badge.info { color: #7c3aed; }
.log-badge.warn { color: #eab308; }
.log-badge.req { color: #a78bfa; }
.log-text { color: #8b7aa8; }
.log-box::-webkit-scrollbar { width: 4px; }
.log-box::-webkit-scrollbar-thumb { background: #7c3aed; border-radius: 2px; }
.log-entry { border-bottom: 1px solid rgba(124,58,237,0.05); padding: 2px 0; }
/* ── Overlay URLs ── */
.url-list { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; }
.url-item { font-size: 11px; color: #6d5a8a; padding: 6px 10px;
  background: rgba(0,0,0,0.2); border-radius: 4px; word-break: break-all;
  cursor: pointer; transition: background 0.2s; }
.url-item:hover { background: rgba(124,58,237,0.12); }
.url-item code { color: #a78bfa; }
.url-item .hint { float: right; font-size: 9px; color: #4a3a6a; margin-top: 1px; }
</style>
</head>
<body>
<div class="sidebar">
  <div class="clock" id="clock">--:--:--</div>
  <div class="clock-date" id="clock-date">---</div>
  <img class="avatar" id="avatar" src="" alt="" style="display:none">
  <h1>Stream Manager</h1>
  <div class="sub"><span id="display-name">NeoTheFox98</span> · <span id="view-count">0</span> views</div>
  <div class="nav-item active">Overview</div>
  <div class="nav-item">Overlays</div>
  <div class="nav-item">Settings</div>
  <div style="margin-top:auto;padding-top:16px;border-top:1px solid rgba(124,58,237,0.1)">
    <div style="font-size:11px;color:#6d5a8a">Server</div>
    <div style="font-size:12px;color:#8b7aa8;margin-top:2px">
      ● <span id="server-status">Running</span> <span id="server-port">:5000</span>
    </div>
    <div style="font-size:11px;color:#6d5a8a;margin-top:4px" id="server-uptime-row">
      Uptime: <span id="server-uptime">0s</span>
    </div>
    <div style="font-size:11px;color:#6d5a8a;margin-top:6px">Twitch API</div>
    <div style="font-size:12px;margin-top:2px">
      <span class="status-dot" id="twitch-api-dot"></span>
      <span id="twitch-api-label">Checking...</span>
    </div>
  </div>
</div>
<div class="main">
  <div class="row">
    <div class="card">
      <h3>OBS Studio</h3>
      <div><span class="status-dot" id="obs-dot"></span><span id="obs-label">Checking...</span></div>
      <div class="stat-value" style="font-size:18px" id="obs-pid"></div>
      <div class="stat-label" id="obs-uptime">Waiting...</div>
    </div>
    <div class="card">
      <h3>Twitch Stream</h3>
      <div><span class="status-dot" id="twitch-dot"></span><span id="twitch-label">Checking...</span></div>
      <div class="twitch-title" id="twitch-title">—</div>
      <div class="twitch-game" id="twitch-game">—</div>
      <div class="stat-value" id="twitch-viewers" style="font-size:22px"></div>
      <div class="stat-label" id="twitch-uptime"></div>
    </div>
    <div class="card">
      <h3>System</h3>
      <div class="stat-value"><span id="cpu-pct">0</span><span class="unit">% CPU</span></div>
      <div class="bar-bg"><div class="bar-fill" id="cpu-bar" style="width:0%"></div></div>
      <div class="stat-value" style="font-size:18px;margin-top:8px">
        <span id="ram-used">0</span><span class="unit"> / </span><span id="ram-total">0</span><span class="unit"> GB</span>
      </div>
      <div class="bar-bg"><div class="bar-fill" id="ram-bar" style="width:0%"></div></div>
      <div class="stat-label" id="ram-pct-label">0% used</div>
      <div class="stat-label" style="margin-top:6px">GPU: <span id="gpu-name" style="color:#c4b5fd">—</span></div>
    </div>
  </div>

  <div class="card" style="flex:none">
    <h3>Overlay URLs</h3>
    <div class="url-list">
      <div class="url-item" onclick="copyUrl(this)" title="Click to copy full URL"><code>/overlays/starting-soon.html</code><span class="hint">Copy</span></div>
      <div class="url-item" onclick="copyUrl(this)" title="Click to copy full URL"><code>/overlays/be-right-back.html</code><span class="hint">Copy</span></div>
      <div class="url-item" onclick="copyUrl(this)" title="Click to copy full URL"><code>/overlays/stream-ending.html</code><span class="hint">Copy</span></div>
      <div class="url-item" onclick="copyUrl(this)" title="Click to copy full URL"><code>/overlays/tech-difficulties.html</code><span class="hint">Copy</span></div>
    </div>
  </div>

  <div class="card log-card">
    <h3>Request Log</h3>
    <div class="log-box" id="log-box">
      <div class="log-entry">Waiting for data...</div>
    </div>
  </div>
</div>

<script>
function updateClock() {
  const now = new Date();
  document.getElementById('clock').textContent = now.toLocaleTimeString();
  document.getElementById('clock-date').textContent = now.toLocaleDateString(undefined, { weekday:'long', month:'long', day:'numeric' });
}
setInterval(updateClock, 1000);
updateClock();

function copyUrl(el) {
  const url = window.location.origin + el.querySelector('code').textContent;
  navigator.clipboard.writeText(url).then(() => {
    const orig = el.innerHTML;
    el.innerHTML = '<span style="color:#22c55e">✓ Copied!</span>';
    setTimeout(() => el.innerHTML = orig, 1200);
  }).catch(() => {});
}
function fmtUptime(secs) {
  if (!secs || secs <= 0) return '';
  const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60), s = secs % 60;
  return h ? h+'h '+m+'m' : m ? m+'m '+s+'s' : s+'s';
}

function twCard(el) {
  if (!el) return;
  const live = document.getElementById('twitch-label')?.textContent === 'LIVE';
  el.classList.toggle('card-live', live);
}

async function poll() {
  try {
    const r = await fetch('/api/status');
    const s = await r.json();

    // OBS
    const obsDot = document.getElementById('obs-dot');
    const obsLabel = document.getElementById('obs-label');
    obsDot.className = 'status-dot ' + (s.obs.running ? 'on' : 'off');
    obsLabel.textContent = s.obs.running ? 'Running' : 'Not running';
    document.getElementById('obs-pid').textContent = s.obs.running ? 'PID ' + s.obs.pid : '';
    document.getElementById('obs-uptime').textContent = s.obs.running ? 'Uptime: ' + fmtUptime(s.obs.uptime) : '';

    // Twitch stream
    const twDot = document.getElementById('twitch-dot');
    const twLabel = document.getElementById('twitch-label');
    const twLive = s.twitch.live;
    twDot.className = 'status-dot ' + (twLive ? 'on' : 'off');
    twLabel.textContent = twLive ? 'LIVE' : 'Offline';
    document.getElementById('twitch-title').textContent = s.twitch.title || '—';
    document.getElementById('twitch-game').textContent = s.twitch.game || '—';
    document.getElementById('twitch-viewers').textContent = twLive ? s.twitch.viewers + ' viewers' : '';
    document.getElementById('twitch-uptime').textContent = twLive ? s.twitch.uptime : '';

    // Twitch user info
    if (s.twitch.display_name) {
      document.getElementById('display-name').textContent = s.twitch.display_name;
    }
    if (s.twitch.view_count) {
      document.getElementById('view-count').textContent = s.twitch.view_count.toLocaleString();
    }
    const avatar = document.getElementById('avatar');
    if (s.twitch.profile_image_url) {
      avatar.src = s.twitch.profile_image_url;
      avatar.style.display = 'block';
    }

    // Twitch API status
    const apiDot = document.getElementById('twitch-api-dot');
    const apiLabel = document.getElementById('twitch-api-label');
    apiDot.className = 'status-dot ' + (s.twitch.connected ? 'on' : 'off');
    apiLabel.textContent = s.twitch.connected ? 'Connected' : 'No credentials';

    // Live glow on Twitch card
    twCard(document.querySelector('.card:nth-child(2)'));

    // Server uptime
    document.getElementById('server-uptime').textContent = s.server.uptime || '0s';
    document.getElementById('server-port').textContent = ':' + s.server.port;

    // System
    document.getElementById('cpu-pct').textContent = s.system.cpu;
    document.getElementById('cpu-bar').style.width = s.system.cpu + '%';

    const ramPct = s.system.ram_pct;
    document.getElementById('ram-used').textContent = s.system.ram_used_gb;
    document.getElementById('ram-total').textContent = s.system.ram_total_gb;
    document.getElementById('ram-bar').style.width = ramPct + '%';
    document.getElementById('ram-pct-label').textContent = ramPct + '% used';

    // GPU
    const gpuEl = document.getElementById('gpu-name');
    if (gpuEl && s.system.gpu) gpuEl.textContent = s.system.gpu;

    // Log
    const logBox = document.getElementById('log-box');
    if (s.requests && s.requests.length) {
      logBox.innerHTML = s.requests.map(r => {
        const m = r.match(/^\[(\d+:\d+:\d+)\]\s+(.*)/);
        if (m) return '<div class="log-entry"><span class="log-timestamp">[' + m[1] + ']</span> <span class="log-text">' + m[2] + '</span></div>';
        return '<div class="log-entry"><span class="log-text">' + r + '</span></div>';
      }).join('');
    }
  } catch(e) {
    document.getElementById('obs-label').textContent = 'Disconnected';
  }
}
setInterval(poll, 2000);
poll();
</script>
</body>
</html>"""

# ── CLI ────────────────────────────────────────────────────────────────
def parse_args():
    p = argparse.ArgumentParser(description="Stream Manager — web dashboard + overlay server + system monitor")
    p.add_argument("--port", type=int, default=0, help="Port to listen on (overrides config.json)")
    p.add_argument("--poll", type=int, default=0, help="Poll interval in seconds (overrides config.json)")
    p.add_argument("--no-browser", action="store_true", help="Don't open dashboard in browser")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args()

def try_bind_port(start):
    """Try to bind HTTP server on start..start+19. Returns (server, port) or raises."""
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
            try:
                _s.bind(("0.0.0.0", port))
            except OSError:
                continue
        s = HTTPServer(("0.0.0.0", port), Handler)
        return s, port
    raise RuntimeError(f"Could not bind to any port in range {start}-{start+19}")

# ── Main ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = parse_args()
    if args.port:
        config["port"] = args.port
    if args.poll:
        config["poll_interval"] = args.poll
    setup_file_logging(config["log_file"])
    server, PORT = try_bind_port(config["port"])
    state["server"]["port"] = PORT
    server.timeout = 0.5

    M = style("M", "┃")
    B = style("D", "─")
    def heading(text): return f"  {M}  {style('W', style('B', text))}"
    def info(k, v):   return f"  {M}    {style('C', k)}  {v}"

    box_top    = f"  {style('M', '┏')}{B*58}{style('M', '┓')}"
    box_bot    = f"  {style('M', '┗')}{B*58}{style('M', '┛')}"
    box_div    = f"  {style('M', '┣')}{B*58}{style('M', '┫')}"
    separator  = f"  {style('D', '─')*60}"
    blank_row  = f"  {M}  {'':55s}{M}"

    # Pre-check services
    get_obs_status()
    get_system_stats()
    get_gpu_stats()
    token = _twitch_oauth()
    tw_ok = token is not None
    if tw_ok:
        get_twitch_status()
        get_twitch_user_info()

    dn = state["twitch"]["display_name"] or TWITCH_USER
    vc = state["twitch"]["view_count"]
    live_str = f"{icon(state['twitch']['live'])} {style('B' if state['twitch']['live'] else 'D', 'LIVE' if state['twitch']['live'] else 'Offline')}"

    nav = [
        ("Dashboard", f"http://localhost:{PORT}/dashboard"),
        ("API",       f"http://localhost:{PORT}/api/status"),
        ("Health",    f"http://localhost:{PORT}/api/health"),
    ]

    overlays = []
    if os.path.isdir(OVERLAYS_DIR):
        overlays = sorted(os.listdir(OVERLAYS_DIR))

    print()
    print(box_top)
    ver = style('D', f'v{__version__}')
    print(f"  {M}  {style('M', style('B', '▄▄  Stream Manager'))}  {ver}     {style('D', 'Web dashboard + overlay server + system monitor')}  {M}")
    print(box_div)
    print(heading("Server"))
    print(info("●", f"http://localhost:{PORT}"))
    for name, url in nav:
        print(info(f" {style('D', '→')}", f"{style('W', name):12s}  {style('D', url)}"))
    print(blank_row)
    print(heading("Channels"))
    print(info("Twitch", f"{style('W', dn):22s} {icon(tw_ok)} {style('G' if tw_ok else 'R', 'Connected' if tw_ok else 'No credentials')}  {live_str}  {style('D', f'{vc:,} views' if vc else '')}"))
    obs_icon = icon(state['obs']['running'])
    obs_str = f"Running (PID {state['obs']['pid']})" if state['obs']['running'] else "Not running"
    print(info("OBS   ", f"{obs_icon} {style('D', obs_str)}"))
    sys_str = f"{state['system']['cpu']}% CPU  ·  {state['system']['ram_used_gb']}/{state['system']['ram_total_gb']} GB RAM"
    gpu_name = state['system']['gpu'] or "—"
    print(info("System", f"{icon(True)} {style('D', sys_str)}  ·  {gpu_name}"))
    print(blank_row)
    print(heading("Overlays"))
    if overlays:
        for ov in overlays:
            print(info(f" {style('D', '▸')}", style('D', f"/overlays/{ov}")))
    else:
        print(info("", style('Y', "No overlays found")))
    print(blank_row)
    print(heading("Polling"))
    print(info(f" {style('D', '↻')}", f"{style('D', f'every {config["poll_interval"]}s')}"))
    print(box_bot)
    print()
    print(separator)
    flags = " --port / --poll / --no-browser"
    print(f"  {style('D', f'Ctrl+C to stop · config.json · flags:{flags}')}")
    print(separator)
    print()

    if not args.no_browser:
        webbrowser.open(f"http://localhost:{PORT}/dashboard")

    try:
        while True:
            server.handle_request()
    except KeyboardInterrupt:
        print(f"\n  {style('Y', 'Shutdown.')}")
        server.server_close()
