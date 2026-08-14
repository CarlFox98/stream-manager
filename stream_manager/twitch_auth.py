"""User-token auth for interactive features (chat + channel-point redemptions).

The dashboard/status side of Stream Manager only needs an *app* access token
(client-credentials, read-only public data — see twitch.py). Chat and channel
points need a *user* access token with scopes, tied to the broadcaster account.

Auth flow: Twitch **Authorization Code Grant** with a local redirect. Stream
Manager opens your browser straight to Twitch's consent screen; you click
"Authorize" once and Twitch redirects back to a tiny callback the app serves at
``/auth/callback`` — no codes to copy, no links to paste. The token is cached
(and silently refreshed) in a gitignored JSON file next to config.json.

Works with either app type:
  • Confidential app (a client secret in .env)  → standard secret exchange.
  • Public app (no usable secret)               → PKCE (code_verifier/challenge).

Docs: https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/
"""
import base64, hashlib, json, os, secrets, threading, time, urllib.error, urllib.parse, urllib.request, webbrowser

from .config import BASE_DIR, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, TWITCH_USER, config

# Scopes the interactive layer needs:
#   chat:read / chat:edit              → read & send chat over IRC
#   channel:read:redemptions           → list channel-point redemptions
#   channel:manage:redemptions         → create rewards & fulfil/refund redemptions
#   bits:read / channel:read:subscriptions → EventSub for bits/subs (hype overlay)
#   channel:manage:vips                → automated VIP outcome
#   moderator:manage:banned_users      → automated timeout outcome (risky wheel)
#   moderator:manage:shoutouts         → automated shoutout outcome
SCOPES = [
    "chat:read",
    "chat:edit",
    "channel:read:redemptions",
    "channel:manage:redemptions",
    "bits:read",
    "channel:read:subscriptions",
    "channel:manage:vips",
    "moderator:manage:banned_users",
    "moderator:manage:shoutouts",
]

TOKEN_FILE = os.path.join(BASE_DIR, ".twitch_user_token.json")

_AUTH_URL = "https://id.twitch.tv/oauth2/authorize"
_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
CALLBACK_PATH = "/auth/callback"

# In-memory auth state. `status` is one of:
#   "unconfigured" (no client id), "unauthorized" (needs login),
#   "pending" (browser open, waiting for the redirect back), "ok", "error".
auth = {
    "status": "unconfigured",
    "access_token": None,
    "refresh_token": None,
    "expires_at": 0,
    "login": "",              # broadcaster login name (lowercase)
    "user_id": "",            # broadcaster_id used for Helix calls
    "authorize_url": "",      # link the dashboard can offer as a manual fallback
    "error": "",
}

_lock = threading.Lock()

# Set once the HTTP server has bound a port (see cli.py). The redirect URI must
# match EXACTLY what you registered in the Twitch dev console, so it's derived
# from the real bound port.
_redirect_uri = f"http://localhost:{config.get('port', 5000)}{CALLBACK_PATH}"
# Per-attempt CSRF/PKCE material, set when a login is started.
_pending = {"state": "", "verifier": ""}


def set_server_port(port):
    """Point the OAuth redirect at the port the server actually bound."""
    global _redirect_uri
    _redirect_uri = f"http://localhost:{port}{CALLBACK_PATH}"


def redirect_uri():
    return _redirect_uri


# ── low-level HTTP ────────────────────────────────────────────────────────
def _http_form(url, fields):
    """POST an x-www-form-urlencoded body, return (status_code, parsed_json)."""
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
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


# ── PKCE helpers ──────────────────────────────────────────────────────────
def _pkce_pair():
    """Return (verifier, challenge) for PKCE (S256)."""
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def build_authorize_url(state, challenge=None):
    """Pure builder for the Twitch consent URL (kept small for testing)."""
    params = {
        "client_id": TWITCH_CLIENT_ID,
        "redirect_uri": _redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "force_verify": "false",
    }
    if challenge:
        params["code_challenge"] = challenge
        params["code_challenge_method"] = "S256"
    return f"{_AUTH_URL}?{urllib.parse.urlencode(params)}"


# ── token persistence ─────────────────────────────────────────────────────
def _save_tokens():
    try:
        with open(TOKEN_FILE, "w") as f:
            json.dump({
                "access_token": auth["access_token"],
                "refresh_token": auth["refresh_token"],
                "expires_at": auth["expires_at"],
                "login": auth["login"],
                "user_id": auth["user_id"],
                "scopes": SCOPES,
            }, f)
        try:
            os.chmod(TOKEN_FILE, 0o600)  # best effort; ignored on Windows
        except OSError:
            pass
    except Exception as e:
        print(f"[auth] Could not save token file: {e}")


def _load_tokens():
    if not os.path.isfile(TOKEN_FILE):
        return False
    try:
        with open(TOKEN_FILE) as f:
            d = json.load(f)
    except Exception:
        return False
    # Re-auth if the saved token was minted for a different scope set.
    if set(d.get("scopes", [])) != set(SCOPES):
        return False
    auth["access_token"] = d.get("access_token")
    auth["refresh_token"] = d.get("refresh_token")
    auth["expires_at"] = d.get("expires_at", 0)
    auth["login"] = d.get("login", "")
    auth["user_id"] = d.get("user_id", "")
    return bool(auth["refresh_token"])


# ── identity ──────────────────────────────────────────────────────────────
def _fetch_identity():
    """Populate login + user_id from the token via Helix /users."""
    token = auth["access_token"]
    if not token:
        return False
    req = urllib.request.Request(
        "https://api.twitch.tv/helix/users",
        headers={"Client-ID": TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read())
        if body.get("data"):
            u = body["data"][0]
            auth["login"] = (u.get("login") or "").lower()
            auth["user_id"] = u.get("id", "")
            return True
    except Exception as e:
        print(f"[auth] identity lookup failed: {e}")
    return False


# ── refresh ───────────────────────────────────────────────────────────────
def _token_request(extra):
    """POST to the token endpoint, trying with the client secret first (needed
    by Confidential apps) and falling back to a secret-free request (Public apps
    / PKCE). Returns (code, body) of the first success. If all attempts fail,
    returns the FIRST attempt's error — for a Confidential app that's the
    meaningful "invalid client secret", not the generic "missing client secret"
    the secret-free fallback would report."""
    base = {"client_id": TWITCH_CLIENT_ID, **extra}
    attempts = []
    if TWITCH_CLIENT_SECRET:
        attempts.append({**base, "client_secret": TWITCH_CLIENT_SECRET})
    attempts.append(base)  # secret-free / PKCE-only
    first = None
    for fields in attempts:
        code, body = _http_form(_TOKEN_URL, fields)
        if code == 200 and body.get("access_token"):
            return code, body
        if first is None:
            first = (code, body)
    return first


def _refresh():
    if not auth["refresh_token"]:
        return False
    code, body = _token_request({
        "grant_type": "refresh_token",
        "refresh_token": auth["refresh_token"],
    })
    if code == 200 and body.get("access_token"):
        auth["access_token"] = body["access_token"]
        auth["refresh_token"] = body.get("refresh_token", auth["refresh_token"])
        auth["expires_at"] = time.time() + body.get("expires_in", 3600)
        _save_tokens()
        return True
    # Refresh token no longer valid → force a fresh login.
    auth["refresh_token"] = None
    return False


def get_user_token():
    """Return a valid user access token, refreshing if needed, else None."""
    with _lock:
        if auth["access_token"] and time.time() < auth["expires_at"] - 120:
            return auth["access_token"]
        if _refresh():
            return auth["access_token"]
        return None


# ── authorization-code flow ───────────────────────────────────────────────
def begin_authorization(open_browser=True):
    """Start (or restart) the browser login. Returns the authorize URL or None.

    Opens the user's default browser straight to Twitch's consent screen. When
    they approve, Twitch redirects to CALLBACK_PATH, which handle_callback()
    finishes. The URL is also stored so the dashboard can show a manual link if
    the browser didn't pop up (e.g. a headless box).
    """
    if not TWITCH_CLIENT_ID:
        auth["status"] = "unconfigured"
        return None
    state = secrets.token_urlsafe(24)
    # Always use PKCE. It's required for Public apps and harmless for
    # Confidential ones, so the same login works regardless of app type.
    verifier, challenge = _pkce_pair()
    with _lock:
        _pending["state"] = state
        _pending["verifier"] = verifier
    url = build_authorize_url(state, challenge)
    auth["authorize_url"] = url
    auth["status"] = "pending"
    auth["error"] = ""
    print("\n  ┏━ Twitch login ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  ┃  A browser window is opening for you to authorize.")
    print("  ┃  If it doesn't, open this link:")
    print(f"  ┃  {url}")
    print("  ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception as e:
            print(f"[auth] could not open browser automatically: {e}")
    return url


def _exchange_code(code):
    """Swap an authorization code for tokens. Returns True on success."""
    extra = {
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": _redirect_uri,
    }
    verifier = _pending.get("verifier")
    if verifier:
        extra["code_verifier"] = verifier
    status_code, body = _token_request(extra)
    if status_code == 200 and body.get("access_token"):
        auth["access_token"] = body["access_token"]
        auth["refresh_token"] = body.get("refresh_token")
        auth["expires_at"] = time.time() + body.get("expires_in", 3600)
        return True
    auth["error"] = body.get("message") or body.get("_error") or f"token exchange failed ({status_code})"
    return False


def handle_callback(query):
    """Handle the redirect back from Twitch. `query` is the raw query string.

    Returns (ok, human_message). Verifies the state parameter, exchanges the
    code, and looks up the broadcaster identity. Safe to call from the HTTP
    handler thread.
    """
    params = urllib.parse.parse_qs(query or "")
    if params.get("error"):
        auth["status"] = "unauthorized"
        auth["error"] = params.get("error_description", ["authorization denied"])[0]
        return False, auth["error"]
    code = (params.get("code") or [""])[0]
    state = (params.get("state") or [""])[0]
    with _lock:
        expected = _pending.get("state", "")
    if not code or not state or not expected or not secrets.compare_digest(state, expected):
        auth["status"] = "error"
        auth["error"] = "login response didn't match this session — please try again"
        return False, auth["error"]
    with _lock:
        if not _exchange_code(code):
            auth["status"] = "error"
            return False, auth["error"]
        # one-time use — clear so a replayed redirect can't re-run
        _pending["state"] = ""
        _pending["verifier"] = ""
    if _fetch_identity():
        auth["status"] = "ok"
        auth["error"] = ""
        auth["authorize_url"] = ""
        _save_tokens()
        print(f"[auth] Authorized as {auth['login']} (id {auth['user_id']}).")
        return True, f"Authorized as {auth['login']}"
    auth["status"] = "error"
    auth["error"] = "token obtained but identity lookup failed"
    return False, auth["error"]


def logout():
    """Forget the cached token and require a fresh login."""
    with _lock:
        auth.update({"access_token": None, "refresh_token": None, "expires_at": 0,
                     "login": "", "user_id": "", "status": "unauthorized", "error": ""})
        _pending["state"] = ""
        _pending["verifier"] = ""
    try:
        if os.path.isfile(TOKEN_FILE):
            os.remove(TOKEN_FILE)
    except OSError as e:
        print(f"[auth] could not remove token file: {e}")
    return True


def initialize(auto_login=True):
    """Load cached tokens; if none/invalid and auto_login, open the browser login.

    Returns True if a usable token is available immediately.
    """
    if not TWITCH_CLIENT_ID:
        auth["status"] = "unconfigured"
        return False
    if _load_tokens():
        if get_user_token() and (auth["user_id"] or _fetch_identity()):
            auth["status"] = "ok"
            _save_tokens()
            return True
    auth["status"] = "unauthorized"
    if auto_login:
        begin_authorization(open_browser=True)
    return False


def public_status():
    """Auth summary safe to expose on the dashboard (no tokens)."""
    return {
        "status": auth["status"],
        "login": auth["login"] or TWITCH_USER,
        "user_id": auth["user_id"],
        "authorize_url": auth["authorize_url"] if auth["status"] in ("pending", "unauthorized", "error") else "",
        "error": auth["error"],
        "scopes": SCOPES,
    }
