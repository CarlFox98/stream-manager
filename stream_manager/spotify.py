"""Spotify now-playing — powers the !song command and the dashboard widget.

Uses Spotify's Authorization Code flow with a local ``/auth/spotify/callback``
redirect (one-click, same UX as the Twitch login). Works with a Confidential
app (client secret) or a Public app (PKCE). Only read scopes are requested; we
never control playback.

Setup: create an app at https://developer.spotify.com/dashboard, add the
redirect URL the dashboard shows, and put SPOTIFY_CLIENT_ID (and optionally
SPOTIFY_CLIENT_SECRET) in .env.
"""
import base64, hashlib, json, os, secrets, threading, time, urllib.error, urllib.parse, urllib.request, webbrowser

from .config import BASE_DIR, SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET

SCOPES = ["user-read-currently-playing", "user-read-playback-state"]
TOKEN_FILE = os.path.join(BASE_DIR, ".spotify_token.json")
CALLBACK_PATH = "/auth/spotify/callback"

_AUTH_URL = "https://accounts.spotify.com/authorize"
_TOKEN_URL = "https://accounts.spotify.com/api/token"
_NOW_URL = "https://api.spotify.com/v1/me/player/currently-playing"

auth = {
    "status": "unconfigured",   # unconfigured | unauthorized | pending | ok | error
    "access_token": None, "refresh_token": None, "expires_at": 0,
    "authorize_url": "", "error": "",
}
_lock = threading.Lock()
_redirect_uri = "http://localhost:5000" + CALLBACK_PATH
_pending = {"state": "", "verifier": ""}


def set_server_port(port):
    global _redirect_uri
    _redirect_uri = f"http://localhost:{port}{CALLBACK_PATH}"


def redirect_uri():
    return _redirect_uri


def configured():
    return bool(SPOTIFY_CLIENT_ID)


# ── HTTP helpers ────────────────────────────────────────────────────────────
def _token_post(fields):
    data = urllib.parse.urlencode(fields).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if SPOTIFY_CLIENT_SECRET:
        basic = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
        headers["Authorization"] = "Basic " + basic
    else:
        fields.setdefault("client_id", SPOTIFY_CLIENT_ID)
        data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(_TOKEN_URL, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"_error": str(e)}


def _pkce_pair():
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


# ── token persistence ───────────────────────────────────────────────────────
def _save():
    try:
        with open(TOKEN_FILE, "w") as f:
            json.dump({"access_token": auth["access_token"], "refresh_token": auth["refresh_token"],
                       "expires_at": auth["expires_at"], "scopes": SCOPES}, f)
        try:
            os.chmod(TOKEN_FILE, 0o600)
        except OSError:
            pass
    except Exception as e:
        print(f"[spotify] could not save token: {e}")


def _load():
    if not os.path.isfile(TOKEN_FILE):
        return False
    try:
        with open(TOKEN_FILE) as f:
            d = json.load(f)
    except Exception:
        return False
    if set(d.get("scopes", [])) != set(SCOPES):
        return False
    auth["access_token"] = d.get("access_token")
    auth["refresh_token"] = d.get("refresh_token")
    auth["expires_at"] = d.get("expires_at", 0)
    return bool(auth["refresh_token"])


def _refresh():
    if not auth["refresh_token"]:
        return False
    code, body = _token_post({"grant_type": "refresh_token", "refresh_token": auth["refresh_token"]})
    if code == 200 and body.get("access_token"):
        auth["access_token"] = body["access_token"]
        auth["refresh_token"] = body.get("refresh_token", auth["refresh_token"])
        auth["expires_at"] = time.time() + body.get("expires_in", 3600)
        _save()
        return True
    auth["refresh_token"] = None
    return False


def _token():
    with _lock:
        if auth["access_token"] and time.time() < auth["expires_at"] - 60:
            return auth["access_token"]
        if _refresh():
            return auth["access_token"]
        return None


# ── auth-code flow ──────────────────────────────────────────────────────────
def begin_authorization(open_browser=True):
    if not SPOTIFY_CLIENT_ID:
        auth["status"] = "unconfigured"
        return None
    state = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    with _lock:
        _pending["state"] = state
        _pending["verifier"] = verifier
    params = {"client_id": SPOTIFY_CLIENT_ID, "response_type": "code",
              "redirect_uri": _redirect_uri, "scope": " ".join(SCOPES), "state": state,
              "code_challenge_method": "S256", "code_challenge": challenge}
    url = f"{_AUTH_URL}?{urllib.parse.urlencode(params)}"
    auth["authorize_url"] = url
    auth["status"] = "pending"
    auth["error"] = ""
    print(f"\n  [spotify] Authorize now-playing: {url}\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    return url


def handle_callback(query):
    params = urllib.parse.parse_qs(query or "")
    if params.get("error"):
        auth["status"] = "unauthorized"
        auth["error"] = params.get("error", ["denied"])[0]
        return False, auth["error"]
    code = (params.get("code") or [""])[0]
    state = (params.get("state") or [""])[0]
    with _lock:
        expected = _pending.get("state", "")
        verifier = _pending.get("verifier", "")
    if not code or not state or not secrets.compare_digest(state, expected):
        auth["status"] = "error"
        auth["error"] = "login response didn't match — try again"
        return False, auth["error"]
    fields = {"grant_type": "authorization_code", "code": code, "redirect_uri": _redirect_uri}
    if verifier and not SPOTIFY_CLIENT_SECRET:
        fields["code_verifier"] = verifier
    scode, body = _token_post(fields)
    if scode == 200 and body.get("access_token"):
        auth["access_token"] = body["access_token"]
        auth["refresh_token"] = body.get("refresh_token")
        auth["expires_at"] = time.time() + body.get("expires_in", 3600)
        auth["status"] = "ok"
        auth["authorize_url"] = ""
        auth["error"] = ""
        _pending["state"] = _pending["verifier"] = ""
        _save()
        print("[spotify] connected.")
        return True, "Spotify connected"
    auth["status"] = "error"
    auth["error"] = body.get("error_description") or body.get("_error") or f"token exchange failed ({scode})"
    return False, auth["error"]


def logout():
    with _lock:
        auth.update({"access_token": None, "refresh_token": None, "expires_at": 0,
                     "status": "unauthorized", "error": ""})
    try:
        if os.path.isfile(TOKEN_FILE):
            os.remove(TOKEN_FILE)
    except OSError:
        pass
    return True


def initialize():
    if not SPOTIFY_CLIENT_ID:
        auth["status"] = "unconfigured"
        return
    if _load() and _token():
        auth["status"] = "ok"
    else:
        auth["status"] = "unauthorized"


# ── now playing ─────────────────────────────────────────────────────────────
def now_playing():
    """Return {"playing", "title", "artist", "url"} or {} if nothing/unavailable."""
    token = _token()
    if not token:
        return {}
    req = urllib.request.Request(_NOW_URL, headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            if r.status == 204:
                return {"playing": False}
            body = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == 204:
            return {"playing": False}
        return {}
    except Exception:
        return {}
    item = body.get("item") or {}
    if not item:
        return {"playing": bool(body.get("is_playing"))}
    artists = ", ".join(a.get("name", "") for a in item.get("artists", []) if a.get("name"))
    return {
        "playing": bool(body.get("is_playing")),
        "title": item.get("name", ""),
        "artist": artists,
        "url": (item.get("external_urls") or {}).get("spotify", ""),
    }


def song_line():
    """One-line '!song' reply, or None if nothing is playing / not connected."""
    np = now_playing()
    if not np or not np.get("title"):
        return None
    return f"🎵 Now playing: {np['title']} — {np['artist']}".strip(" —")


def public_status():
    np = now_playing() if auth["status"] == "ok" else {}
    return {
        "configured": configured(),
        "status": auth["status"],
        "authorize_url": auth["authorize_url"] if auth["status"] in ("pending", "unauthorized", "error") else "",
        "error": auth["error"],
        "now": np,
    }
