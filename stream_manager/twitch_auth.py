"""User-token auth for interactive features (chat + channel-point redemptions).

The dashboard/status side of Stream Manager only needs an *app* access token
(client-credentials, read-only public data — see twitch.py). Chat and channel
points need a *user* access token with scopes, tied to the broadcaster account.

We use Twitch's Device Code Grant Flow (DCF): no client secret, no OAuth
redirect handling — the user opens a URL, types a short code, and we poll for
the token. Tokens are cached (and refreshed) in a gitignored JSON file next to
config.json.

Docs: https://dev.twitch.tv/docs/authentication/getting-tokens-device-code-grant-flow/
"""
import json, os, threading, time, urllib.error, urllib.parse, urllib.request

from .config import BASE_DIR, TWITCH_CLIENT_ID, TWITCH_USER

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

_DEVICE_URL = "https://id.twitch.tv/oauth2/device"
_TOKEN_URL = "https://id.twitch.tv/oauth2/token"

# In-memory auth state. `status` is one of:
#   "unconfigured" (no client id), "unauthorized" (needs device login),
#   "pending" (waiting for the user to enter the code), "ok", "error".
auth = {
    "status": "unconfigured",
    "access_token": None,
    "refresh_token": None,
    "expires_at": 0,
    "login": "",              # bot/broadcaster login name (lowercase)
    "user_id": "",            # broadcaster_id used for Helix calls
    "verification_uri": "",   # shown to the user while pending
    "user_code": "",
    "error": "",
}

_lock = threading.Lock()


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


# ── token persistence ────────────────────────────────────────────────────
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


# ── identity ─────────────────────────────────────────────────────────────
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


# ── refresh ──────────────────────────────────────────────────────────────
def _refresh():
    if not auth["refresh_token"]:
        return False
    code, body = _http_form(_TOKEN_URL, {
        "client_id": TWITCH_CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": auth["refresh_token"],
    })
    if code == 200 and body.get("access_token"):
        auth["access_token"] = body["access_token"]
        auth["refresh_token"] = body.get("refresh_token", auth["refresh_token"])
        auth["expires_at"] = time.time() + body.get("expires_in", 3600)
        _save_tokens()
        return True
    # Refresh token no longer valid → force a fresh device login.
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


# ── device code flow ─────────────────────────────────────────────────────
def _start_device_flow():
    """Kick off DCF; returns (device_code, interval, expires_in) or None."""
    code, body = _http_form(_DEVICE_URL, {
        "client_id": TWITCH_CLIENT_ID,
        "scopes": " ".join(SCOPES),
    })
    if code != 200 or "device_code" not in body:
        auth["status"] = "error"
        auth["error"] = body.get("message") or body.get("_error") or f"device request failed ({code})"
        return None
    auth["verification_uri"] = body.get("verification_uri", "https://www.twitch.tv/activate")
    auth["user_code"] = body.get("user_code", "")
    auth["status"] = "pending"
    return body["device_code"], body.get("interval", 5), body.get("expires_in", 1800)


def _poll_device_token(device_code, interval, expires_in):
    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(max(interval, 1))
        code, body = _http_form(_TOKEN_URL, {
            "client_id": TWITCH_CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })
        if code == 200 and body.get("access_token"):
            auth["access_token"] = body["access_token"]
            auth["refresh_token"] = body.get("refresh_token")
            auth["expires_at"] = time.time() + body.get("expires_in", 3600)
            if _fetch_identity():
                auth["status"] = "ok"
                auth["user_code"] = ""
                _save_tokens()
                print(f"[auth] Authorized as {auth['login']} (id {auth['user_id']}).")
                return True
            auth["status"] = "error"
            auth["error"] = "token obtained but identity lookup failed"
            return False
        msg = body.get("message", "")
        if msg == "authorization_pending":
            continue
        if msg == "slow_down":
            interval += 2
            continue
        # expired_token / access_denied / anything else → stop.
        auth["status"] = "unauthorized"
        auth["error"] = msg or "device authorization did not complete"
        return False
    auth["status"] = "unauthorized"
    auth["error"] = "device code expired"
    return False


def begin_authorization(blocking=False):
    """Start (or restart) the device login. Prints instructions to the console.

    Non-blocking by default: spawns a daemon thread that polls for the token so
    startup isn't held up. Returns the human-facing (verification_uri, user_code).
    """
    if not TWITCH_CLIENT_ID:
        auth["status"] = "unconfigured"
        return None
    started = _start_device_flow()
    if not started:
        return None
    device_code, interval, expires_in = started
    uri, ucode = auth["verification_uri"], auth["user_code"]
    print("\n  ┏━ Twitch interactive login ━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  ┃  1. Open:  {uri}")
    print(f"  ┃  2. Enter code:  {ucode}")
    print("  ┃  (Grants chat + channel-point access for this account.)")
    print("  ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    if blocking:
        return _poll_device_token(device_code, interval, expires_in)
    threading.Thread(
        target=_poll_device_token,
        args=(device_code, interval, expires_in),
        daemon=True,
    ).start()
    return uri, ucode


def initialize(auto_login=True):
    """Load cached tokens; if none/invalid and auto_login, start device flow.

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
        begin_authorization(blocking=False)
    return False


def public_status():
    """Auth summary safe to expose on the dashboard (no tokens)."""
    return {
        "status": auth["status"],
        "login": auth["login"] or TWITCH_USER,
        "user_id": auth["user_id"],
        "verification_uri": auth["verification_uri"] if auth["status"] in ("pending", "unauthorized") else "",
        "user_code": auth["user_code"],
        "error": auth["error"],
        "scopes": SCOPES,
    }
