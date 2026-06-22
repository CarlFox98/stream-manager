#!/usr/bin/env python3
"""
Stream Manager — web dashboard + overlay server + system monitor
"""

import json, os, subprocess, time, threading, urllib.request, urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────
ASSETS_DIR = os.path.expandvars(r"%USERPROFILE%\Pictures\OBS Assets")
OVERLAYS_DIR = os.path.join(ASSETS_DIR, "overlays")
PORT = 5000
TWITCH_USER = "NeoTheFox98"
POLL_INTERVAL = 5  # seconds

# ── State ─────────────────────────────────────────────────────────────
state = {
    "obs": {"running": False, "pid": None, "uptime": 0},
    "twitch": {"live": False, "title": "", "game": "", "viewers": 0},
    "system": {"cpu": 0, "ram_pct": 0, "ram_used_gb": 0, "ram_total_gb": 0, "gpu": ""},
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
        # Fallback: WMI
        try:
            out = subprocess.run(["wmic", "cpu", "get", "loadpercentage"],
                                 capture_output=True, text=True, timeout=3).stdout
            lines = [l.strip() for l in out.splitlines() if l.strip().isdigit()]
            state["system"]["cpu"] = int(lines[0]) if lines else 0
        except: pass
        try:
            out = subprocess.run(["wmic", "OS", "get", "FreePhysicalMemory,TotalVisibleMemorySize"],
                                 capture_output=True, text=True, timeout=3).stdout
            parts = out.split()
            for i, p in enumerate(parts):
                if p.isdigit():
                    kb = int(p)
                    if i == 1: total_kb = kb
                    if i == 2: free_kb = kb
            if total_kb:
                state["system"]["ram_total_gb"] = round(total_kb / 1048576, 1)
                state["system"]["ram_pct"] = round((total_kb - free_kb) / total_kb * 100, 1)
                state["system"]["ram_used_gb"] = round((total_kb - free_kb) / 1048576, 1)
        except: pass

def get_twitch_status():
    try:
        url = f"https://api.twitch.tv/helix/search/channels?query={TWITCH_USER}"
        req = urllib.request.Request(url, headers={"User-Agent": "StreamManager/1.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
            for ch in data.get("data", []):
                if ch["broadcaster_login"].lower() == TWITCH_USER.lower():
                    state["twitch"]["live"] = ch.get("is_live", False)
                    state["twitch"]["title"] = ch.get("title", "")
                    state["twitch"]["game"] = ch.get("game_name", "")
                    return
    except: pass

def poll_loop():
    while True:
        get_obs_status()
        get_system_stats()
        get_twitch_status()
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

        # Serve overlay files
        if self.path.startswith("/overlays/"):
            rel = self.path.lstrip("/")
            filepath = os.path.join(ASSETS_DIR, rel)
            if os.path.isfile(filepath):
                self.serve_file(filepath, "text/html")
                self.log(f"Served overlay: {rel}")
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
        self.log("Served dashboard")

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

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        state["requests"].insert(0, entry)
        if len(state["requests"]) > 100:
            state["requests"] = state["requests"][:100]
        print(entry)

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
  <div class="nav-item" style="margin-top:auto;padding-top:16px;border-top:1px solid rgba(124,58,237,0.1)">
    <div style="font-size:11px;color:#6d5a8a">Server</div>
    <div style="font-size:12px;color:#8b7aa8;margin-top:2px">
      ● <span id="server-status">Running</span> :5000
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
    </div>
  </div>

  <div class="card" style="flex:none">
    <h3>Overlay URLs</h3>
    <div class="url-list">
      <div class="url-item"><code>/overlays/starting-soon.html</code></div>
      <div class="url-item"><code>/overlays/be-right-back.html</code></div>
      <div class="url-item"><code>/overlays/stream-ending.html</code></div>
      <div class="url-item"><code>/overlays/tech-difficulties.html</code></div>
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
    twDot.className = 'status-dot ' + (s.twitch.live ? 'on' : 'off');
    twLabel.textContent = s.twitch.live ? 'LIVE' : 'Offline';
    document.getElementById('twitch-title').textContent = s.twitch.title || '—';
    document.getElementById('twitch-game').textContent = s.twitch.game || '—';
    document.getElementById('twitch-viewers').textContent = s.twitch.live ? s.twitch.viewers + ' viewers' : '';

    // System
    document.getElementById('cpu-pct').textContent = s.system.cpu;
    document.getElementById('cpu-bar').style.width = s.system.cpu + '%';

    const ramPct = s.system.ram_pct;
    document.getElementById('ram-used').textContent = s.system.ram_used_gb;
    document.getElementById('ram-total').textContent = s.system.ram_total_gb;
    document.getElementById('ram-bar').style.width = ramPct + '%';
    document.getElementById('ram-pct-label').textContent = ramPct + '% used';

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

# ── Main ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"🎬 Stream Manager starting on http://localhost:{PORT}")
    print(f"📊 Dashboard: http://localhost:{PORT}/dashboard")
    print(f"🔌 Overlays served from: {OVERLAYS_DIR}")
    print("─" * 50)
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutdown.")
        server.server_close()
