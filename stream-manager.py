#!/usr/bin/env python3
"""
Stream Manager — web dashboard + overlay server + system monitor
"""

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

# ── Config ────────────────────────────────────────────────────────────
ASSETS_DIR = os.path.expandvars(r"%USERPROFILE%\Pictures\OBS Assets")
OVERLAYS_DIR = os.path.join(ASSETS_DIR, "overlays")
PORT = 5000
TWITCH_USER = "NeoTheFox98"
POLL_INTERVAL = 5  # seconds

# ── config.env / env vars ────────────────────────────────────────────
_load_env = os.path.join(os.path.dirname(__file__), ".env")
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
    "twitch": {"live": False, "title": "", "game": "", "viewers": 0, "started_at": "", "uptime": "", "connected": False},
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
        else:
            state["obs"]["running"] = False
            state["obs"]["pid"] = None
    except: state["obs"]["running"] = False

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
        state["server"]["uptime"] = compute_uptime(state["server"]["started_at"])
        time.sleep(POLL_INTERVAL)

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

        # Serve overlay files
        if self.path.startswith("/overlays/"):
            rel = self.path.lstrip("/")
            filepath = os.path.join(ASSETS_DIR, rel)
            if os.path.isfile(filepath):
                self.serve_file(filepath, "text/html")
                self.log(f"Served overlay: {rel}", "→")
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
  color: #8b7aa8; cursor: pointer; transition: all 0.2s;
}
.sidebar .nav-item:hover, .sidebar .nav-item.active {
  background: rgba(124,58,237,0.15); color: #c4b5fd;
}
/* ── Main ── */
.main { flex: 1; display: flex; flex-direction: column; gap: 20px; max-width: 900px; }
.row { display: flex; gap: 16px; flex-wrap: wrap; }
.card {
  background: rgba(13,0,22,0.85); border: 1px solid rgba(124,58,237,0.15);
  border-radius: 14px; padding: 20px; flex: 1; min-width: 200px;
  position: relative; overflow: hidden;
}
.card::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
  background: linear-gradient(90deg, #7c3aed, #a78bfa);
}
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
  line-height: 1.6; color: #8b7aa8;
}
.log-box::-webkit-scrollbar { width: 4px; }
.log-box::-webkit-scrollbar-thumb { background: #7c3aed; border-radius: 2px; }
.log-entry { border-bottom: 1px solid rgba(124,58,237,0.05); padding: 2px 0; }
/* ── Overlay URLs ── */
.url-list { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; }
.url-item { font-size: 11px; color: #6d5a8a; padding: 4px 8px;
  background: rgba(0,0,0,0.2); border-radius: 4px; word-break: break-all; }
.url-item code { color: #a78bfa; }
</style>
</head>
<body>
<div class="sidebar">
  <h1>🎬 Stream Manager</h1>
  <div class="sub">NeoTheFox98</div>
  <div class="nav-item active">📊 Overview</div>
  <div class="nav-item">🔌 Overlays</div>
  <div class="nav-item">⚙️ Settings</div>
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
      <div><span class="status-dot" id="obs-dot">●</span><span id="obs-label">Checking...</span></div>
      <div class="stat-value" id="obs-pid"></div>
      <div class="stat-label" id="obs-uptime"></div>
    </div>
    <div class="card">
      <h3>Twitch Stream</h3>
      <div><span class="status-dot" id="twitch-dot">●</span><span id="twitch-label">Checking...</span></div>
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
      <div class="url-item" onclick="copyUrl(this)" title="Click to copy full URL"><code>/overlays/starting-soon.html</code></div>
      <div class="url-item" onclick="copyUrl(this)" title="Click to copy full URL"><code>/overlays/be-right-back.html</code></div>
      <div class="url-item" onclick="copyUrl(this)" title="Click to copy full URL"><code>/overlays/stream-ending.html</code></div>
      <div class="url-item" onclick="copyUrl(this)" title="Click to copy full URL"><code>/overlays/tech-difficulties.html</code></div>
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
function copyUrl(el) {
  const url = window.location.origin + el.querySelector('code').textContent;
  navigator.clipboard.writeText(url).then(() => {
    const orig = el.innerHTML;
    el.innerHTML = '<span style="color:#22c55e">✓ Copied!</span>';
    setTimeout(() => el.innerHTML = orig, 1200);
  }).catch(() => {});
}
async function poll() {
  try {
    const r = await fetch('/api/status');
    const s = await r.json();

    // OBS
    const obsDot = document.getElementById('obs-dot');
    const obsLabel = document.getElementById('obs-label');
    obsDot.className = 'status-dot ' + (s.obs.running ? 'on' : 'off');
    obsLabel.textContent = s.obs.running ? 'Running (PID ' + s.obs.pid + ')' : 'Not running';
    document.getElementById('obs-pid').textContent = s.obs.running ? '●' : '○';
    document.getElementById('obs-uptime').textContent = '';

    // Twitch
    const twDot = document.getElementById('twitch-dot');
    const twLabel = document.getElementById('twitch-label');
    const twLive = s.twitch.live;
    twDot.className = 'status-dot ' + (twLive ? 'on' : 'off');
    twLabel.textContent = twLive ? 'LIVE' : 'Offline';
    document.getElementById('twitch-title').textContent = s.twitch.title || '—';
    document.getElementById('twitch-game').textContent = s.twitch.game || '—';
    document.getElementById('twitch-viewers').textContent = twLive ? s.twitch.viewers + ' viewers' : '';
    document.getElementById('twitch-uptime').textContent = twLive ? s.twitch.uptime : '';

    // Twitch API status
    const apiDot = document.getElementById('twitch-api-dot');
    const apiLabel = document.getElementById('twitch-api-label');
    apiDot.className = 'status-dot ' + (s.twitch.connected ? 'on' : 'off');
    apiLabel.textContent = s.twitch.connected ? 'Connected' : 'No credentials';

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
      logBox.innerHTML = s.requests.map(r => '<div class="log-entry">' + r + '</div>').join('');
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
    p.add_argument("--port", type=int, default=5000, help="Port to listen on (default: 5000)")
    p.add_argument("--no-browser", action="store_true", help="Don't open dashboard in browser")
    return p.parse_args()

def find_port(start):
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start

# ── Main ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = parse_args()
    PORT = find_port(args.port)
    state["server"]["port"] = PORT

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

    live_str = f"{icon(state['twitch']['live'])} {style('B' if state['twitch']['live'] else 'D', 'LIVE' if state['twitch']['live'] else 'Offline')}"

    nav = [
        ("Dashboard", f"http://localhost:{PORT}/dashboard"),
        ("API",       f"http://localhost:{PORT}/api/status"),
        ("Health",    f"http://localhost:{PORT}/api/health"),
    ]

    overlays = []
    if os.path.isdir(OVERLAYS_DIR):
        overlays = sorted(f for f in os.listdir(OVERLAYS_DIR) if f.endswith(".html"))

    print()
    print(box_top)
    print(f"  {M}  {style('M', style('B', '▄▄  Stream Manager'))}       {style('D', 'Web dashboard + overlay server + system monitor')}  {M}")
    print(box_div)
    print(heading("Server"))
    print(info("●", f"http://localhost:{PORT}"))
    for name, url in nav:
        print(info(f" {style('D', '→')}", f"{style('W', name):12s}  {style('D', url)}"))
    print(blank_row)
    print(heading("Channels"))
    print(info("Twitch", f"{style('W', TWITCH_USER):22s} {icon(tw_ok)} {style('G' if tw_ok else 'R', 'Connected' if tw_ok else 'No credentials')}  {live_str}"))
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
    print(info(f" {style('D', '↻')}", f"{style('D', f'every {POLL_INTERVAL}s')}"))
    print(box_bot)
    print()
    print(separator)
    print(f"  {style('D', f'Ctrl+C to stop  ·  --port / --no-browser flags available')}")
    print(separator)
    print()

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.timeout = 0.5

    if not args.no_browser:
        webbrowser.open(f"http://localhost:{PORT}/dashboard")

    try:
        while True:
            server.handle_request()
    except KeyboardInterrupt:
        print(f"\n  {style('Y', 'Shutdown.')}")
        server.server_close()
