#!/usr/bin/env python3
"""
Stream Manager — web dashboard + overlay server + system monitor
"""
__version__ = "0.2.0"

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

# ── Scene sets ────────────────────────────────────────────────────────
# Layout on disk:
#   overlays/modern/   full set of scene .html files (modern neon theme)
#   overlays/retro/    full set of scene .html files (retro Win98 theme)
#   overlays/active/   a copy of whichever set is currently live
# OBS points at /overlays/active/<scene>.html and never changes; switching
# a set means replacing the contents of active/ with the chosen set.
import hashlib, shutil

SCENE_SETS = ["modern", "retro"]
ACTIVE_DIRNAME = "active"
ACTIVE_DIR = os.path.join(OVERLAYS_DIR, ACTIVE_DIRNAME)
# Manifest records which set was last applied to active/ (source of truth).
ACTIVE_MANIFEST = os.path.join(ACTIVE_DIR, ".active-set")

def set_dir(name):
    return os.path.join(OVERLAYS_DIR, name)

def available_sets():
    """Return the subset of SCENE_SETS that actually exist on disk with files."""
    out = []
    for name in SCENE_SETS:
        d = set_dir(name)
        if os.path.isdir(d) and any(f.lower().endswith(".html") for f in os.listdir(d)):
            out.append(name)
    return out

def _dir_signature(d):
    """Stable hash of a directory's *.html filenames + contents, for fallback detection."""
    if not os.path.isdir(d):
        return None
    h = hashlib.sha256()
    for fn in sorted(os.listdir(d)):
        if not fn.lower().endswith(".html"):
            continue
        h.update(fn.encode("utf-8"))
        try:
            with open(os.path.join(d, fn), "rb") as f:
                h.update(f.read())
        except OSError:
            pass
    return h.hexdigest()

def detect_active_set():
    """
    Return the name of the set currently in active/, or None if unknown/empty.
    Prefers the manifest; falls back to content-matching against known sets.
    """
    if not os.path.isdir(ACTIVE_DIR):
        return None
    # 1. Trust the manifest if present and valid
    try:
        with open(ACTIVE_MANIFEST, encoding="utf-8") as f:
            name = f.read().strip()
        if name in SCENE_SETS:
            return name
    except OSError:
        pass
    # 2. Fallback: match active/ contents against each known set
    active_sig = _dir_signature(ACTIVE_DIR)
    if active_sig:
        for name in SCENE_SETS:
            if _dir_signature(set_dir(name)) == active_sig:
                return name
    return None

def apply_scene_set(name):
    """
    Copy overlays/<name>/ into overlays/active/, replacing it, and write the
    manifest. Returns (ok, message). Never raises to the caller.
    """
    if name not in SCENE_SETS:
        return False, f"Unknown scene set '{name}'"
    src = set_dir(name)
    if not os.path.isdir(src):
        return False, f"Scene set '{name}' not found on disk"
    try:
        os.makedirs(ACTIVE_DIR, exist_ok=True)
        # Clear existing active/ contents (files + subdirs), keep the dir itself
        for entry in os.listdir(ACTIVE_DIR):
            p = os.path.join(ACTIVE_DIR, entry)
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                try: os.remove(p)
                except OSError: pass
        # Copy the chosen set in
        for entry in os.listdir(src):
            s = os.path.join(src, entry)
            d = os.path.join(ACTIVE_DIR, entry)
            if os.path.isdir(s):
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)
        with open(ACTIVE_MANIFEST, "w", encoding="utf-8") as f:
            f.write(name)
        return True, f"Switched active scene set to '{name}'"
    except Exception as e:
        return False, f"Failed to switch scene set: {e}"

# ── Update checking / self-update ─────────────────────────────────────
# Checks GitHub Releases for a newer tagged version and can download +
# install it, but NEVER installs without explicit confirmation. The running
# file is backed up before any swap, and the download is validated as real
# Python before it's allowed to replace anything.
GITHUB_OWNER = "CarlFox98"
GITHUB_REPO = "stream-manager"
GITHUB_API_LATEST = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
UPDATE_ASSET_NAME = "stream-manager.py"   # the release asset (or raw file) to fetch
SELF_PATH = os.path.abspath(__file__)

update_state = {"checked": False, "latest": None, "current": __version__,
                "available": False, "error": None, "notes": ""}

def _parse_version(v):
    """'v1.2.3' or '1.2.3' -> (1,2,3). Non-numeric parts sort as 0."""
    v = (v or "").lstrip("vV").strip()
    parts = []
    for chunk in v.split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit(): num += ch
            else: break
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])

def _http_get(url, accept=None, timeout=8):
    req = urllib.request.Request(url, headers={
        "User-Agent": f"stream-manager/{__version__}",
        "Accept": accept or "*/*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def check_for_update():
    """Query GitHub Releases. Populates update_state. Read-only, never writes."""
    update_state["checked"] = True
    update_state["error"] = None
    try:
        data = json.loads(_http_get(GITHUB_API_LATEST, accept="application/vnd.github+json"))
        tag = data.get("tag_name") or ""
        update_state["latest"] = tag
        update_state["notes"] = (data.get("body") or "").strip()[:500]
        # find the download URL for our asset, else fall back to the raw tag file
        dl = None
        for asset in data.get("assets", []):
            if asset.get("name") == UPDATE_ASSET_NAME:
                dl = asset.get("browser_download_url"); break
        if not dl and tag:
            dl = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{tag}/{UPDATE_ASSET_NAME}"
        update_state["download_url"] = dl
        update_state["available"] = bool(tag) and _parse_version(tag) > _parse_version(__version__)
        return update_state
    except urllib.error.HTTPError as e:
        update_state["error"] = f"GitHub returned HTTP {e.code}" + (" (no releases yet?)" if e.code == 404 else "")
    except Exception as e:
        update_state["error"] = str(e)
    update_state["available"] = False
    return update_state

def _validate_python_source(text):
    """Reject anything that isn't a plausible, parseable stream-manager.py."""
    if not text or len(text) < 500:
        return False, "Downloaded file is too small to be valid"
    if "__version__" not in text or "Stream Manager" not in text:
        return False, "Downloaded file doesn't look like stream-manager.py"
    try:
        import ast as _ast
        _ast.parse(text)
    except SyntaxError as e:
        return False, f"Downloaded file has a syntax error: {e}"
    return True, "ok"

def download_update():
    """
    Download the new version to a staging file next to the current one.
    Returns (ok, message, staged_path|None). Does NOT install.
    """
    url = update_state.get("download_url")
    if not url:
        return False, "No download URL — run a version check first", None
    try:
        raw = _http_get(url).decode("utf-8", "replace")
    except Exception as e:
        return False, f"Download failed: {e}", None
    ok, why = _validate_python_source(raw)
    if not ok:
        return False, why, None
    staged = SELF_PATH + ".new"
    try:
        with open(staged, "w", encoding="utf-8", newline="\n") as f:
            f.write(raw)
    except OSError as e:
        return False, f"Could not write staged file: {e}", None
    return True, f"Downloaded {update_state.get('latest')} to {os.path.basename(staged)}", staged

def install_update(staged_path):
    """
    Back up the current file and swap in the staged one. Caller is responsible
    for having obtained confirmation first. Returns (ok, message).
    """
    if not staged_path or not os.path.isfile(staged_path):
        return False, "No staged update found — download first"
    # Re-validate the staged file right before install (belt and suspenders)
    try:
        with open(staged_path, encoding="utf-8") as f:
            ok, why = _validate_python_source(f.read())
        if not ok:
            return False, f"Refusing to install: {why}"
    except OSError as e:
        return False, f"Could not read staged file: {e}"
    backup = SELF_PATH + ".bak"
    try:
        shutil.copy2(SELF_PATH, backup)          # backup current
        shutil.copy2(staged_path, SELF_PATH)     # swap in new
        os.remove(staged_path)                   # clean staging
        return True, f"Installed update. Previous version backed up to {os.path.basename(backup)}. Restart to apply."
    except Exception as e:
        return False, f"Install failed ({e}); your current file is unchanged"

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
    "scenes": {"active_set": None, "available": []},
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

        if self.path == "/api/scenes":
            state["scenes"]["available"] = available_sets()
            state["scenes"]["active_set"] = detect_active_set()
            self.serve_json(state["scenes"]); return

        if self.path == "/api/update":
            check_for_update()
            self.serve_json({
                "current": update_state["current"],
                "latest": update_state["latest"],
                "available": update_state["available"],
                "error": update_state["error"],
                "notes": update_state["notes"],
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

    def do_POST(self):
        if self.path == "/api/scenes/switch":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                body = json.loads(raw.decode("utf-8") or "{}")
            except (ValueError, json.JSONDecodeError):
                self.serve_json({"ok": False, "error": "Invalid request body"}, status=400); return

            name = body.get("set", "")
            ok, msg = apply_scene_set(name)
            state["scenes"]["available"] = available_sets()
            state["scenes"]["active_set"] = detect_active_set()
            self.log(msg, "✓" if ok else "✗")
            self.serve_json({
                "ok": ok, "message": msg,
                "active_set": state["scenes"]["active_set"],
                "available": state["scenes"]["available"],
            }, status=200 if ok else 400)
            return

        if self.path == "/api/update/install":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                body = json.loads(raw.decode("utf-8") or "{}")
            except (ValueError, json.JSONDecodeError):
                self.serve_json({"ok": False, "error": "Invalid request body"}, status=400); return

            # Require explicit confirmation — never install on a bare request
            if body.get("confirm") is not True:
                self.serve_json({"ok": False, "error": "Confirmation required (confirm: true)"}, status=400); return

            check_for_update()
            if not update_state.get("available"):
                self.serve_json({"ok": False, "error": "No newer version available"}, status=400); return

            ok, msg, staged = download_update()
            if not ok:
                self.log(f"Update download failed: {msg}", "✗")
                self.serve_json({"ok": False, "error": msg}, status=400); return

            ok, msg = install_update(staged)
            self.log(msg, "✓" if ok else "✗")
            self.serve_json({"ok": ok, "message": msg,
                             "installed_version": update_state.get("latest") if ok else None},
                            status=200 if ok else 400)
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

    def serve_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
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
/* ── Scene set switcher ── */
.scene-active { font-size: 20px; font-weight: 800; color: #c4b5fd; margin: 2px 0 2px; }
.scene-active.unknown { color: #f59e0b; }
.scene-sub { font-size: 11px; color: #6d5a8a; margin-bottom: 14px; }
.scene-btns { display: flex; gap: 8px; flex-wrap: wrap; }
.scene-btn {
  flex: 1; min-width: 96px; padding: 10px 14px; border-radius: 10px;
  font-family: inherit; font-size: 13px; font-weight: 700; cursor: pointer;
  color: #8b7aa8; background: rgba(0,0,0,0.25);
  border: 1px solid rgba(124,58,237,0.2); transition: all 0.18s;
}
.scene-btn:hover:not(:disabled) { background: rgba(124,58,237,0.15); color: #c4b5fd; }
.scene-btn.current {
  background: linear-gradient(135deg, rgba(124,58,237,0.35), rgba(168,85,247,0.2));
  color: #fff; border-color: rgba(168,85,247,0.5);
}
.scene-btn:disabled { opacity: 0.5; cursor: default; }
.scene-msg { font-size: 11px; margin-top: 10px; min-height: 14px; color: #6d5a8a; }
.scene-msg.ok { color: #22c55e; }
.scene-msg.err { color: #f87171; }
/* ── Update notice ── */
.update-card { display: none; border-color: rgba(245,158,11,0.4) !important; }
.update-card.show { display: block; }
.update-card h3 { color: #f59e0b; }
.update-ver { font-size: 15px; font-weight: 700; color: #fbbf24; margin: 4px 0 2px; }
.update-sub { font-size: 11px; color: #6d5a8a; margin-bottom: 12px; }
.update-btn {
  padding: 9px 18px; border-radius: 10px; font-family: inherit; font-size: 13px;
  font-weight: 700; cursor: pointer; color: #1a1200;
  background: linear-gradient(135deg, #fbbf24, #f59e0b); border: none;
}
.update-btn:disabled { opacity: 0.6; cursor: default; }
.update-msg { font-size: 11px; margin-top: 10px; min-height: 14px; color: #6d5a8a; }
.update-msg.ok { color: #22c55e; } .update-msg.err { color: #f87171; }
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

  <div class="card update-card" id="update-card" style="flex:none">
    <h3>Update Available</h3>
    <div class="update-ver" id="update-ver">—</div>
    <div class="update-sub">A newer version is on GitHub. Your current file is backed up before installing.</div>
    <button class="update-btn" id="update-btn" onclick="installUpdate()">Download &amp; Install</button>
    <div class="update-msg" id="update-msg"></div>
  </div>

  <div class="card" style="flex:none">
    <h3>Scene Set</h3>
    <div class="scene-active unknown" id="scene-active">Detecting…</div>
    <div class="scene-sub">Active theme served at <code>/overlays/active/</code></div>
    <div class="scene-btns" id="scene-btns"></div>
    <div class="scene-msg" id="scene-msg"></div>
  </div>

  <div class="card" style="flex:none">
    <h3>Overlay URLs</h3>
    <div class="scene-sub">Point OBS at these — they stay the same across theme switches.</div>
    <div class="url-list">
      <div class="url-item" onclick="copyUrl(this)" title="Click to copy full URL"><code>/overlays/active/starting-soon.html</code><span class="hint">Copy</span></div>
      <div class="url-item" onclick="copyUrl(this)" title="Click to copy full URL"><code>/overlays/active/be-right-back.html</code><span class="hint">Copy</span></div>
      <div class="url-item" onclick="copyUrl(this)" title="Click to copy full URL"><code>/overlays/active/stream-ending.html</code><span class="hint">Copy</span></div>
      <div class="url-item" onclick="copyUrl(this)" title="Click to copy full URL"><code>/overlays/active/tech-difficulties.html</code><span class="hint">Copy</span></div>
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

const SCENE_LABELS = { modern: 'Modern Neon', retro: 'Retro Win98' };
let sceneSwitching = false;

function renderScenes(data) {
  const active = data.active_set;
  const available = data.available || [];
  const activeEl = document.getElementById('scene-active');
  if (active) {
    activeEl.textContent = SCENE_LABELS[active] || active;
    activeEl.classList.remove('unknown');
  } else {
    activeEl.textContent = 'Unknown / not set';
    activeEl.classList.add('unknown');
  }
  const btnWrap = document.getElementById('scene-btns');
  const order = ['modern', 'retro'];
  const sets = order.filter(n => available.includes(n)).concat(available.filter(n => !order.includes(n)));
  btnWrap.innerHTML = sets.map(name => {
    const label = SCENE_LABELS[name] || name;
    const cur = name === active ? ' current' : '';
    const dis = (sceneSwitching || name === active) ? ' disabled' : '';
    return '<button class="scene-btn' + cur + '" ' + dis + ' onclick="switchScene(\'' + name + '\')">'
      + (name === active ? '● ' : '') + label + '</button>';
  }).join('') || '<span class="scene-sub">No scene sets found on disk.</span>';
}

async function switchScene(name) {
  if (sceneSwitching) return;
  sceneSwitching = true;
  const msg = document.getElementById('scene-msg');
  msg.className = 'scene-msg'; msg.textContent = 'Switching to ' + (SCENE_LABELS[name] || name) + '…';
  try {
    const r = await fetch('/api/scenes/switch', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ set: name })
    });
    const d = await r.json();
    msg.textContent = d.message || (d.ok ? 'Switched.' : 'Switch failed.');
    msg.classList.add(d.ok ? 'ok' : 'err');
    renderScenes(d);
    if (d.ok) msg.textContent += ' — refresh your OBS browser sources.';
  } catch (e) {
    msg.className = 'scene-msg err'; msg.textContent = 'Switch request failed.';
  } finally {
    sceneSwitching = false;
    setTimeout(() => { if (!sceneSwitching) { msg.className = 'scene-msg'; msg.textContent = ''; } }, 6000);
  }
}

async function pollScenes() {
  if (sceneSwitching) return;  // don't clobber the UI mid-switch
  try {
    const r = await fetch('/api/scenes');
    renderScenes(await r.json());
  } catch (e) { /* leave last state */ }
}
setInterval(pollScenes, 4000);
pollScenes();

let updateInfo = null;
async function checkUpdate() {
  try {
    const r = await fetch('/api/update');
    const d = await r.json();
    updateInfo = d;
    const card = document.getElementById('update-card');
    if (d.available && d.latest) {
      document.getElementById('update-ver').textContent = d.latest + '  (current v' + d.current + ')';
      card.classList.add('show');
    } else {
      card.classList.remove('show');
    }
  } catch (e) { /* offline check — ignore */ }
}
async function installUpdate() {
  if (!updateInfo || !updateInfo.available) return;
  const ok = confirm('Download and install ' + updateInfo.latest + '?\n\nYour current file will be backed up to stream-manager.py.bak. You\'ll need to restart Stream Manager afterward.');
  if (!ok) return;
  const btn = document.getElementById('update-btn');
  const msg = document.getElementById('update-msg');
  btn.disabled = true; msg.className = 'update-msg'; msg.textContent = 'Downloading & installing…';
  try {
    const r = await fetch('/api/update/install', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: true })
    });
    const d = await r.json();
    msg.textContent = d.message || d.error || (d.ok ? 'Installed.' : 'Failed.');
    msg.classList.add(d.ok ? 'ok' : 'err');
    if (d.ok) msg.textContent += ' Restart Stream Manager to apply.';
    else btn.disabled = false;
  } catch (e) {
    msg.className = 'update-msg err'; msg.textContent = 'Install request failed.';
    btn.disabled = false;
  }
}
// Check on load, then hourly (GitHub check is cheap and read-only)
checkUpdate();
setInterval(checkUpdate, 3600000);

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
    p.add_argument("--check-update", action="store_true", help="Check GitHub for a newer version and exit")
    p.add_argument("--update", action="store_true", help="Check, then (after confirmation) download & install the latest version")
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

    # ── Update flags (handled before starting the server) ──
    if args.check_update or args.update:
        print(f"\n  {style('C', 'Checking for updates…')}  (current v{__version__})")
        check_for_update()
        if update_state["error"]:
            print(f"  {style('R', '✗')} Update check failed: {update_state['error']}\n")
            sys.exit(1)
        if not update_state["available"]:
            print(f"  {style('G', '✓')} You're on the latest version (v{__version__}).\n")
            sys.exit(0)
        latest = update_state["latest"]
        print(f"  {style('Y', '●')} New version available: {style('B', latest)}  (you have v{__version__})")
        if update_state["notes"]:
            print(f"  {style('D', 'Release notes:')}\n{update_state['notes']}")
        if args.check_update and not args.update:
            print(f"\n  Run {style('W', 'python stream-manager.py --update')} to install it.\n")
            sys.exit(0)
        # --update: confirm, then download + install
        try:
            ans = input(f"\n  Download and install {latest}? Your current file will be backed up. [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = "n"
        if ans not in ("y", "yes"):
            print("  Update cancelled.\n"); sys.exit(0)
        ok, msg, staged = download_update()
        if not ok:
            print(f"  {style('R', '✗')} {msg}\n"); sys.exit(1)
        ok, msg = install_update(staged)
        mark = style('G', '✓') if ok else style('R', '✗')
        print(f"  {mark} {msg}\n")
        sys.exit(0 if ok else 1)

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
        ("Scenes",    f"http://localhost:{PORT}/api/scenes"),
        ("Update",    f"http://localhost:{PORT}/api/update"),
        ("Health",    f"http://localhost:{PORT}/api/health"),
    ]

    # Scene-set detection for the banner
    scene_avail = available_sets()
    scene_active = detect_active_set()
    state["scenes"]["available"] = scene_avail
    state["scenes"]["active_set"] = scene_active

    overlays = []
    if os.path.isdir(ACTIVE_DIR):
        overlays = sorted(f for f in os.listdir(ACTIVE_DIR) if f.lower().endswith(".html"))

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
    print(heading("Scene Set"))
    _set_labels = {"modern": "Modern Neon", "retro": "Retro Win98"}
    if scene_active:
        _lbl = _set_labels.get(scene_active, scene_active)
        print(info("Active", f"{icon(True)} {style('B', style('C', _lbl))}"))
    else:
        print(info("Active", f"{icon(False)} {style('Y', 'Unknown / not set')}"))
    if scene_avail:
        _av = ", ".join(_set_labels.get(n, n) + (style('G', ' ✓') if n == scene_active else "") for n in scene_avail)
        print(info("Sets  ", style('D', _av)))
    else:
        print(info("Sets  ", style('Y', "none found — create overlays/modern/ and overlays/retro/")))
    print(blank_row)
    print(heading("Overlays"))
    if overlays:
        for ov in overlays:
            print(info(f" {style('D', '▸')}", style('D', f"/overlays/{ACTIVE_DIRNAME}/{ov}")))
    else:
        print(info("", style('Y', "No overlays in active/ — switch a scene set from the dashboard")))
    print(blank_row)
    print(heading("Polling"))
    print(info(f" {style('D', '↻')}", f"{style('D', f'every {config["poll_interval"]}s')}"))
    print(box_bot)
    print()
    print(separator)
    flags = " --port / --poll / --no-browser / --check-update / --update"
    print(f"  {style('D', f'Ctrl+C to stop · config.json · flags:{flags}')}")
    print(separator)
    print()

    # Non-blocking startup update check — a slow/failed GitHub call never delays startup
    def _bg_update_check():
        check_for_update()
        if update_state.get("available"):
            msg = f"Update available: {update_state['latest']} (you have v{__version__}) — run --update or use the dashboard"
            print(f"  {style('Y', '●')} {style('Y', msg)}\n")
    threading.Thread(target=_bg_update_check, daemon=True).start()

    if not args.no_browser:
        webbrowser.open(f"http://localhost:{PORT}/dashboard")

    try:
        while True:
            server.handle_request()
    except KeyboardInterrupt:
        print(f"\n  {style('Y', 'Shutdown.')}")
        server.server_close()
