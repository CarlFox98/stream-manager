"""HTTP request routing: dashboard, JSON API, and safe static/overlay file serving."""
import json, os, socket, urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

from . import scenes, updater
from . import actions, chat, effects, eventsub, games, quotes, redeems, stats, twitch_auth
from .config import BASE_DIR, OVERLAYS_DIR, config
from .console import style
from .logging_util import write_file_log
from .state import state

STATIC_DIR = os.path.join(BASE_DIR, "static")


def interactive_status():
    """Snapshot of the interactive layer for the dashboard (no secrets)."""
    port = state["server"]["port"]
    base = f"http://localhost:{port}/static/interactive"
    return {
        "enabled": bool(config.get("interactive_enabled", True)),
        "prefix": config.get("command_prefix", "!"),
        "auth": twitch_auth.public_status(),
        "chat": {"connected": chat.status["connected"], "channel": chat.status["channel"],
                 "error": chat.status["error"], "sent": chat.status["sent"], "received": chat.status["received"]},
        "redeems": redeems.public_status(),
        "eventsub": eventsub.public_status(),
        "automation": {"enabled": actions.enabled()},
        "quotes": {"count": quotes.count()},
        "recent": effects.history(limit=12),
        "overlays": {
            "coinflip": f"{base}/coinflip.html",
            "wheel": f"{base}/wheel.html",
            "slots": f"{base}/slots.html",
            "hype": f"{base}/hype.html",
        },
    }

MIME_MAP = {
    ".html": "text/html", ".css": "text/css", ".js": "application/javascript",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml", ".json": "application/json",
}


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
            state["scenes"]["available"] = scenes.available_sets()
            state["scenes"]["active_set"] = scenes.detect_active_set()
            self.serve_json(state["scenes"]); return

        if self.path == "/api/update":
            updater.check_for_update()
            self.serve_json({
                "current": updater.update_state["current"],
                "latest": updater.update_state["latest"],
                "available": updater.update_state["available"],
                "error": updater.update_state["error"],
                "notes": updater.update_state["notes"],
            }); return

        # ── interactive layer (games / chat / redeems) ──
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/api/effects/"):
            channel = parsed.path[len("/api/effects/"):].strip("/")
            qs = urllib.parse.parse_qs(parsed.query)
            try:
                since = int(qs.get("since", ["0"])[0])
            except (ValueError, TypeError):
                since = 0
            self.serve_json(effects.since(channel, since)); return

        if parsed.path == "/api/interactive":
            self.serve_json(interactive_status()); return

        if parsed.path == "/api/quotes":
            self.serve_json({"count": quotes.count(), "quotes": quotes.all_quotes()}); return

        if parsed.path == "/api/interactive/stats":
            self.serve_json(stats.summary()); return

        # Serve overlay / asset files (path-traversal safe)
        if self.path.startswith("/overlays/"):
            if self._serve_safe(self.path[len("/overlays/"):], OVERLAYS_DIR):
                return

        # Serve the dashboard's own CSS/JS (path-traversal safe)
        if self.path.startswith("/static/"):
            if self._serve_safe(self.path[len("/static/"):], STATIC_DIR):
                return

        self.send_response(404); self.end_headers()
        self.wfile.write(b"Not found")

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, json.JSONDecodeError):
            return None

    def do_POST(self):
        # ── interactive layer ──
        if self.path == "/api/interactive/test":
            body = self._read_json()
            if body is None:
                self.serve_json({"ok": False, "error": "Invalid request body"}, status=400); return
            action = body.get("action", "")
            say = chat.say if chat.status.get("connected") else None
            result = games.run_action(action, user=body.get("user", "Dashboard"), say=say)
            self.log(f"Test {action} → {result}", "→" if result is not None else "✗")
            self.serve_json({"ok": result is not None, "action": action, "result": result},
                            status=200 if result is not None else 400)
            return

        if self.path == "/api/interactive/authorize":
            twitch_auth.begin_authorization(blocking=False)
            self.serve_json({"ok": True, "auth": twitch_auth.public_status()}); return

        if self.path == "/api/interactive/reload":
            from . import config as config_mod
            hot = config_mod.reload()
            # push any changed reward costs/limits back onto Twitch
            try:
                redeems.ensure_rewards()
            except Exception as e:
                self.log(f"Config reloaded, reward sync error: {e}", "!")
            self.log("Interactive config reloaded", "✓")
            self.serve_json({"ok": True, "reloaded": list(hot.keys())}); return

        if self.path == "/api/quotes/add":
            body = self._read_json() or {}
            q = quotes.add(body.get("text", ""), added_by=body.get("added_by", "dashboard"))
            self.serve_json({"ok": bool(q), "quote": q}, status=200 if q else 400); return

        if self.path == "/api/quotes/delete":
            body = self._read_json() or {}
            try:
                ok = quotes.delete(int(body.get("id", 0)))
            except (ValueError, TypeError):
                ok = False
            self.serve_json({"ok": ok}, status=200 if ok else 400); return

        if self.path == "/api/scenes/switch":
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                body = json.loads(raw.decode("utf-8") or "{}")
            except (ValueError, json.JSONDecodeError):
                self.serve_json({"ok": False, "error": "Invalid request body"}, status=400); return

            name = body.get("set", "")
            ok, msg = scenes.apply_scene_set(name)
            state["scenes"]["available"] = scenes.available_sets()
            state["scenes"]["active_set"] = scenes.detect_active_set()
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

            updater.check_for_update()
            if not updater.update_state.get("available"):
                self.serve_json({"ok": False, "error": "No newer version available"}, status=400); return

            ok, msg, staged = updater.download_update()
            if not ok:
                self.log(f"Update download failed: {msg}", "✗")
                self.serve_json({"ok": False, "error": msg}, status=400); return

            ok, msg = updater.install_update(staged)
            self.log(msg, "✓" if ok else "✗")
            self.serve_json({"ok": ok, "message": msg,
                             "installed_version": updater.update_state.get("latest") if ok else None},
                            status=200 if ok else 400)
            return

        self.send_response(404); self.end_headers()
        self.wfile.write(b"Not found")

    def serve_dashboard(self):
        path = os.path.join(STATIC_DIR, "dashboard.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)
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

    def _serve_safe(self, rel, root_dir):
        """Serve rel from root_dir if it resolves inside it. Returns True if served."""
        filepath = os.path.normpath(os.path.realpath(os.path.join(root_dir, rel)))
        if os.path.isfile(filepath) and filepath.startswith(root_dir):
            ext = os.path.splitext(filepath)[1].lower()
            mime = MIME_MAP.get(ext, "application/octet-stream")
            self.serve_file(filepath, mime)
            self.log(f"Served: {rel}", "→")
            return True
        return False

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


def try_bind_port(start, host="127.0.0.1"):
    """Try to bind HTTP server on start..start+19. Returns (server, port) or raises."""
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
            try:
                _s.bind((host, port))
            except OSError:
                continue
        s = ThreadingHTTPServer((host, port), Handler)
        s.daemon_threads = True   # don't let in-flight requests block shutdown
        return s, port
    raise RuntimeError(f"Could not bind to any port in range {start}-{start+19}")
