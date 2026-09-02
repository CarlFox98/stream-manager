"""HTTP request routing: dashboard, JSON API, and safe static/overlay file serving."""
import base64, json, os, secrets, socket, urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import scenes, updater
from . import actions, chat, commands, effects, eventsub, games, quotes, redeems, shoutout, spotify, stats, timers, twitch_auth
from . import config as config_mod
from .config import OVERLAYS_DIR, RESOURCE_DIR, DASHBOARD_PASSWORD, config
from .console import style
from .logging_util import write_file_log
from .state import state

STATIC_DIR = os.path.realpath(os.path.join(RESOURCE_DIR, "static"))
_OVERLAYS_ROOT = os.path.realpath(OVERLAYS_DIR)

# Per-run secret. The dashboard is served with this token baked into a <meta>
# tag; its JavaScript echoes it back in an X-SM-Token header on every state-
# changing request. A cross-site page (CSRF) or another device on the LAN can't
# read the token, so it can't drive these endpoints — only the real dashboard,
# loaded same-origin, can. Regenerated every start.
SESSION_TOKEN = secrets.token_urlsafe(32)

# Endpoints that change state and therefore require the session token.
_PROTECTED_POSTS = {
    "/api/interactive/test", "/api/interactive/authorize", "/api/interactive/reload",
    "/api/quotes/add", "/api/quotes/delete", "/api/scenes/switch",
    "/api/update/install", "/auth/logout",
    "/api/commands/toggle", "/api/commands/custom", "/api/config/save",
    "/api/timers/save", "/auth/spotify", "/auth/spotify/logout",
}

# config.json sections the dashboard editor may write.
_EDITABLE_SECTIONS = ("command_prefix", "cooldowns", "wheels", "redeems",
                      "automation", "eventsub", "alerts", "shoutout")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_section(section, data):
    """Friendly per-section validation. Returns (ok, message)."""
    if section == "command_prefix":
        if not isinstance(data, str) or not (1 <= len(data) <= 3) or " " in data:
            return False, "Prefix must be 1–3 characters with no spaces (e.g. !)"
        return True, ""
    if section == "cooldowns":
        for k, v in data.items():
            if k == "mods_bypass":
                continue
            if not isinstance(v, dict):
                return False, f"Cooldown '{k}' must have per-user and global values"
            for scope in ("user", "global"):
                if scope in v and (not _num(v[scope]) or v[scope] < 0):
                    return False, f"Cooldown '{k}' {scope} must be a number ≥ 0"
        return True, ""
    if section == "redeems":
        for k, v in data.items():
            if k in ("auto_fulfill", "refund_on_failure", "catch_up"):
                continue
            if not isinstance(v, dict):
                return False, f"Redeem '{k}' is malformed"
            if "cost" in v and (not _num(v["cost"]) or v["cost"] < 1):
                return False, f"Redeem '{k}' cost must be a number ≥ 1"
        return True, ""
    if section == "wheels":
        for kind, w in data.items():
            if not isinstance(w, dict):
                return False, f"Wheel '{kind}' must be an object"
            segs = w.get("segments")
            if segs is not None:
                if not isinstance(segs, list) or not segs:
                    return False, f"Wheel '{kind}' needs a non-empty 'segments' list"
                for i, s in enumerate(segs):
                    if not isinstance(s, dict) or not str(s.get("label", "")).strip():
                        return False, f"Wheel '{kind}' segment {i+1} needs a 'label'"
                    if "weight" in s and (not _num(s["weight"]) or s["weight"] < 0):
                        return False, f"Wheel '{kind}' segment {i+1} weight must be ≥ 0"
        return True, ""
    if section in ("automation", "eventsub"):
        for k, v in data.items():
            if not isinstance(v, bool):
                return False, f"'{k}' must be on/off"
        return True, ""
    if section == "shoutout":
        for k, v in data.items():
            if k.startswith("template"):
                if not isinstance(v, str):
                    return False, f"'{k}' must be text"
            elif k in ("blocklist", "raid_allowlist"):
                if not isinstance(v, list):
                    return False, f"'{k}' must be a list of logins"
            elif isinstance(v, bool):
                continue
            elif not _num(v):
                return False, f"'{k}' must be a number or on/off"
            elif v < 0:
                return False, f"'{k}' must be ≥ 0"
        return True, ""
    if section == "alerts":
        for k, v in data.items():
            if k.endswith("_message"):
                if not isinstance(v, str):
                    return False, f"'{k}' must be text"
            elif not isinstance(v, bool):
                return False, f"'{k}' must be on/off"
        return True, ""
    return True, ""


def validate_timers(data):
    if not isinstance(data, dict):
        return False, "Timers payload must be an object"
    if "enabled" in data and not isinstance(data["enabled"], bool):
        return False, "'enabled' must be on/off"
    lst = data.get("list", [])
    if not isinstance(lst, list):
        return False, "Timer list must be an array"
    for i, it in enumerate(lst):
        if not isinstance(it, dict):
            return False, f"Timer {i+1} is malformed"
        if not str(it.get("message", "")).strip():
            return False, f"Timer {i+1} needs a message"
        iv = it.get("interval", 15)
        if not _num(iv) or iv <= 0:
            return False, f"Timer {i+1} interval must be a number > 0 (minutes)"
        ml = it.get("min_lines", 0)
        if not _num(ml) or ml < 0:
            return False, f"Timer {i+1} min lines must be ≥ 0"
    return True, ""


def _safe_join(rel, root):
    """Resolve `rel` under `root`, returning the path only if it stays inside.

    URL-decodes first, then resolves symlinks/`..` and confirms the result is
    genuinely within `root` (guards against both traversal and the classic
    sibling-prefix bug, e.g. '/overlays' matching '/overlays-secret').
    """
    root = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root, urllib.parse.unquote(rel)))
    if target == root or target.startswith(root + os.sep):
        return target
    return None


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
        "shoutout": shoutout.public_status(),
        "recent": effects.history(limit=12),
        "overlays": {
            "coinflip": f"{base}/coinflip.html",
            "wheel": f"{base}/wheel.html",
            "slots": f"{base}/slots.html",
            "hype": f"{base}/hype.html",
            "shoutout": f"{base}/shoutout.html",
        },
    }

MIME_MAP = {
    ".html": "text/html", ".css": "text/css", ".js": "application/javascript",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml", ".json": "application/json",
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not self._lan_auth_ok():
            return
        if self.path == "/":
            self.send_response(302); self.send_header("Location", "/dashboard")
            self.end_headers(); return

        if self.path == "/dashboard":
            self.serve_dashboard(); return

        # OAuth redirect back from Twitch (one-click login)
        if self.path.startswith(twitch_auth.CALLBACK_PATH):
            self.serve_auth_callback(); return

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

        # Dashboard live event stream (Server-Sent Events)
        if parsed.path == "/api/stream":
            self.serve_sse(); return

        if parsed.path.startswith("/api/effects/"):
            channel = parsed.path[len("/api/effects/"):].strip("/")
            qs = urllib.parse.parse_qs(parsed.query)
            try:
                since = int(qs.get("since", ["0"])[0])
            except (ValueError, TypeError):
                since = 0
            # Long-poll once the consumer is established (since>0): hold the
            # request open until a new effect fires. First poll (since=0)
            # returns immediately so overlays get the current head id.
            data = effects.wait_events(channel, since) if since > 0 else effects.since(channel, since)
            self.serve_json(data); return

        if parsed.path == "/api/interactive":
            self.serve_json(interactive_status()); return

        if parsed.path == "/api/quotes":
            self.serve_json({"count": quotes.count(), "quotes": quotes.all_quotes()}); return

        if parsed.path == "/api/interactive/stats":
            self.serve_json(stats.summary()); return

        if parsed.path == "/api/commands":
            self.serve_json(commands.public_list()); return

        if parsed.path == "/api/config":
            self.serve_json({s: config.get(s) for s in _EDITABLE_SECTIONS}); return

        if parsed.path == "/api/timers":
            self.serve_json(timers.public_list()); return

        if parsed.path == "/api/shoutout":
            self.serve_json(shoutout.public_status()); return

        if parsed.path == "/api/spotify":
            self.serve_json(spotify.public_status()); return

        if self.path.startswith(spotify.CALLBACK_PATH):
            self.serve_spotify_callback(); return

        # Serve overlay / asset files (path-traversal safe)
        if self.path.startswith("/overlays/"):
            rel = urllib.parse.urlparse(self.path).path[len("/overlays/"):]
            if self._serve_safe(rel, _OVERLAYS_ROOT):
                return

        # Serve the dashboard's own CSS/JS (path-traversal safe)
        if self.path.startswith("/static/"):
            rel = urllib.parse.urlparse(self.path).path[len("/static/"):]
            if self._serve_safe(rel, STATIC_DIR):
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

    def _authorized(self):
        """True if the request carries this run's session token."""
        tok = self.headers.get("X-SM-Token", "")
        return bool(tok) and secrets.compare_digest(tok, SESSION_TOKEN)

    def _lan_auth_ok(self):
        """When --lan + SM_DASHBOARD_PASSWORD are set, require HTTP Basic auth
        from non-localhost clients. Localhost (OBS on this PC) is always exempt.
        Sends a 401 and returns False when auth is required but missing/wrong."""
        if not config.get("lan") or not DASHBOARD_PASSWORD:
            return True
        host = self.client_address[0] if self.client_address else ""
        if host in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return True
        hdr = self.headers.get("Authorization", "")
        if hdr.startswith("Basic "):
            try:
                _, _, pw = base64.b64decode(hdr[6:]).decode("utf-8", "replace").partition(":")
                if secrets.compare_digest(pw, DASHBOARD_PASSWORD):
                    return True
            except Exception:
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Stream Manager"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_POST(self):
        if not self._lan_auth_ok():
            return
        # State-changing endpoints require the dashboard's session token. This
        # blocks CSRF from other sites and requests from other LAN devices.
        if self.path in _PROTECTED_POSTS and not self._authorized():
            self.serve_json({"ok": False, "error": "Not authorized (open the dashboard on this machine)"},
                            status=403)
            return

        if self.path == "/auth/logout":
            twitch_auth.logout()
            self.log("Logged out of Twitch", "!")
            self.serve_json({"ok": True, "auth": twitch_auth.public_status()}); return

        if self.path == "/api/shoutout/clip":
            body = self._read_json() or {}
            state = shoutout.set_clip_playing((body.get("type") or "") == "clipstart")
            self.serve_json({"ok": True, "clip": state}); return

        if self.path == "/auth/spotify":
            spotify.begin_authorization(open_browser=True)
            self.serve_json({"ok": True, "spotify": spotify.public_status()}); return

        if self.path == "/auth/spotify/logout":
            spotify.logout()
            self.log("Disconnected Spotify", "!")
            self.serve_json({"ok": True, "spotify": spotify.public_status()}); return

        # ── interactive layer ──
        if self.path == "/api/interactive/test":
            body = self._read_json()
            if body is None:
                self.serve_json({"ok": False, "error": "Invalid request body"}, status=400); return
            action = body.get("action", "")
            say = chat.say if chat.status.get("connected") else None
            if action == "shoutout":
                effects.emit("shoutout", shoutout.demo_card(), summary="◇ Shoutout: PixelWitch (test)")
                result = "shoutout"
            elif action in ("follow", "firstchat"):
                # fire the hype overlay directly (bypasses alert de-dupe for testing)
                labels = {"follow": "💜 New follower", "firstchat": "👋 First chat"}
                effects.emit("hype", {"kind": action, "user": body.get("user", "Dashboard")},
                             summary=f"{labels[action]}: {body.get('user', 'Dashboard')}")
                result = action
            else:
                result = games.run_action(action, user=body.get("user", "Dashboard"), say=say)
            self.log(f"Test {action} → {result}", "→" if result is not None else "✗")
            self.serve_json({"ok": result is not None, "action": action, "result": result},
                            status=200 if result is not None else 400)
            return

        if self.path == "/api/interactive/authorize":
            twitch_auth.begin_authorization(open_browser=True)
            self.serve_json({"ok": True, "auth": twitch_auth.public_status()}); return

        if self.path == "/api/interactive/reload":
            hot = config_mod.reload()
            # push any changed reward costs/limits back onto Twitch
            try:
                redeems.ensure_rewards()
            except Exception as e:
                self.log(f"Config reloaded, reward sync error: {e}", "!")
            self.log("Interactive config reloaded", "✓")
            self.serve_json({"ok": True, "reloaded": list(hot.keys())}); return

        if self.path == "/api/commands/toggle":
            body = self._read_json() or {}
            name = body.get("name", "")
            ok = commands.set_enabled(name, bool(body.get("enabled", True)))
            if ok:
                self.log(f"Command {name} → {'on' if body.get('enabled') else 'off'}", "✓")
            self.serve_json({"ok": ok, "commands": commands.public_list()},
                            status=200 if ok else 400)
            return

        if self.path == "/api/commands/custom":
            body = self._read_json() or {}
            action = body.get("action", "")
            if action == "delete":
                ok = commands.delete_custom(body.get("name", ""))
                msg = "Deleted" if ok else "Not found"
            else:
                ok, msg = commands.upsert_custom(
                    body.get("name", ""), body.get("response", ""),
                    body.get("permission", "everyone"), bool(body.get("enabled", True)))
            self.log(f"Custom command {body.get('name','')}: {msg}", "✓" if ok else "✗")
            self.serve_json({"ok": ok, "message": msg, "commands": commands.public_list()},
                            status=200 if ok else 400)
            return

        if self.path == "/api/config/save":
            body = self._read_json() or {}
            section = body.get("section", "")
            data = body.get("data")
            if section not in _EDITABLE_SECTIONS:
                self.serve_json({"ok": False, "error": f"Section '{section}' is not editable"}, status=400); return
            expected = str if section == "command_prefix" else dict
            if not isinstance(data, expected):
                self.serve_json({"ok": False, "error": f"'{section}' must be a {expected.__name__}"}, status=400); return
            ok, why = validate_section(section, data)
            if not ok:
                self.serve_json({"ok": False, "error": why}, status=400); return
            try:
                config_mod.save_config({section: data})
                if section in ("redeems",):
                    try: redeems.ensure_rewards()
                    except Exception as e: self.log(f"reward sync after save: {e}", "!")
                self.log(f"Saved config section '{section}'", "✓")
                self.serve_json({"ok": True, "section": section, "data": config.get(section)})
            except Exception as e:
                self.serve_json({"ok": False, "error": str(e)}, status=400)
            return

        if self.path == "/api/timers/save":
            body = self._read_json() or {}
            data = body.get("data")
            ok, why = validate_timers(data)
            if not ok:
                self.serve_json({"ok": False, "error": why}, status=400); return
            try:
                config_mod.save_config({"timers": data})
                self.log("Saved timed messages", "✓")
                self.serve_json({"ok": True, "timers": timers.public_list()})
            except Exception as e:
                self.serve_json({"ok": False, "error": str(e)}, status=400)
            return

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
        # Bake this run's session token into the page so its JS can echo it back.
        html = html.replace("__SM_TOKEN__", SESSION_TOKEN)
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; "
                         "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                         "font-src 'self' https://fonts.gstatic.com; "
                         "script-src 'self'; "
                         "img-src 'self' data: https:; "
                         "connect-src 'self'; "
                         "frame-src 'self'; "
                         "base-uri 'none'; form-action 'self'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)
        self.log("Served dashboard", "→")

    def serve_auth_callback(self):
        """Finish the Twitch one-click login and show a friendly close page."""
        query = urllib.parse.urlparse(self.path).query
        ok, msg = twitch_auth.handle_callback(query)
        self.log(f"Twitch login: {msg}", "✓" if ok else "✗")
        title = "You're all set! ✓" if ok else "Login didn't complete"
        detail = ("Stream Manager is now connected to Twitch. You can close this tab."
                  if ok else f"{msg}. You can close this tab and try again from the dashboard.")
        accent = "#57F2E4" if ok else "#FF7ACB"
        page = f"""<!doctype html><html><head><meta charset="utf-8"><title>Stream Manager</title>
<style>body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0e0b16;color:#eaf0ff;font-family:system-ui,Segoe UI,sans-serif}}
.box{{text-align:center;padding:40px 48px;border:1px solid rgba(124,58,237,.3);border-radius:16px;
background:rgba(30,20,50,.5)}}h1{{color:{accent};margin:0 0 10px;font-size:22px}}
p{{color:#b7a8d6;margin:0}}</style></head>
<body><div class="box"><h1>{title}</h1><p>{detail}</p></div>
<script>setTimeout(function(){{window.close();}},2500);</script></body></html>"""
        body = page.encode()
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def serve_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        # No wildcard CORS: the dashboard is same-origin, and dropping it stops
        # other websites from reading these responses.
        self.end_headers()
        self.wfile.write(body)

    def serve_sse(self):
        """Stream every effect to the dashboard as Server-Sent Events."""
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")   # disable proxy buffering
            self.end_headers()
            self.wfile.write(b": connected\n\n"); self.wfile.flush()
        except OSError:
            return
        last = effects.current_id()
        try:
            while True:
                res = effects.wait_global(last, timeout=20)
                last = res["last_id"]
                if res["events"]:
                    for ev in res["events"]:
                        payload = json.dumps({"channel": ev["channel"], "data": ev["data"],
                                              "summary": ev.get("summary"), "id": ev["id"], "ts": ev["ts"]})
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                else:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def serve_spotify_callback(self):
        """Finish the Spotify one-click connect and show a close page."""
        query = urllib.parse.urlparse(self.path).query
        ok, msg = spotify.handle_callback(query)
        self.log(f"Spotify: {msg}", "✓" if ok else "✗")
        title = "Spotify connected ✓" if ok else "Spotify connection failed"
        detail = ("Now-playing is live. You can close this tab."
                  if ok else f"{msg}. You can close this tab and try again from the dashboard.")
        accent = "#1DB954" if ok else "#FF7ACB"
        page = f"""<!doctype html><html><head><meta charset="utf-8"><title>Spotify</title>
<style>body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0e0b16;color:#eaf0ff;font-family:system-ui,Segoe UI,sans-serif}}
.box{{text-align:center;padding:40px 48px;border:1px solid rgba(124,58,237,.3);border-radius:16px;background:rgba(30,20,50,.5)}}
h1{{color:{accent};margin:0 0 10px;font-size:22px}}p{{color:#b7a8d6;margin:0}}</style></head>
<body><div class="box"><h1>{title}</h1><p>{detail}</p></div>
<script>setTimeout(function(){{window.close();}},2500);</script></body></html>"""
        body = page.encode()
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
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
        filepath = _safe_join(rel, root_dir)
        if filepath and os.path.isfile(filepath):
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


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """A browser source refreshing or a long-poll/SSE being torn down aborts
        the socket mid-response — that's normal, not an error. Swallow the
        connection-reset family instead of dumping a traceback."""
        import sys
        e = sys.exc_info()[1]
        if isinstance(e, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def try_bind_port(start, host="127.0.0.1"):
    """Try to bind HTTP server on start..start+19. Returns (server, port) or raises."""
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
            try:
                _s.bind((host, port))
            except OSError:
                continue
        s = _Server((host, port), Handler)
        return s, port
    raise RuntimeError(f"Could not bind to any port in range {start}-{start+19}")
