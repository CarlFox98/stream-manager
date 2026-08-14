"""Twitch OAuth (app access token) + Helix status/user-info polling.

Status/user-info are public Helix reads, so they work with EITHER token type:
  • the interactive **user** token (from twitch_auth) if you've logged in, or
  • an **app** token via client-credentials (needs a Confidential app + a valid
    client secret).
We prefer the user token when present — that way status still works for Public
apps (which can't mint an app token) or when the client secret is missing/wrong,
and we avoid asking Twitch for a second token we don't need.
"""
import json, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime

from .config import TWITCH_USER, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET

_twitch_token = {"access_token": None, "expires_at": 0}
# When the app-token request keeps failing (e.g. Public app or bad secret),
# back off instead of hammering Twitch every poll, and only warn once.
_app_token_retry_after = 0.0
_app_token_warned = False


def _app_access_token():
    """Client-credentials app token, with failure back-off. None if unavailable."""
    global _app_token_retry_after, _app_token_warned
    now = time.time()
    if _twitch_token["access_token"] and now < _twitch_token["expires_at"] - 60:
        return _twitch_token["access_token"]
    if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
        return None
    if now < _app_token_retry_after:
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
            _app_token_warned = False
            return _twitch_token["access_token"]
    except urllib.error.HTTPError as e:
        _app_token_retry_after = now + 300   # don't retry for 5 min
        if not _app_token_warned:
            _app_token_warned = True
            if e.code == 403:
                print("[twitch] App token refused (403 = invalid client secret). "
                      "Status will use your login token instead once you're authorized. "
                      "To fix app-token status, put the correct TWITCH_CLIENT_SECRET in .env.")
            else:
                print(f"[twitch] App token error (HTTP {e.code}); using your login token for status if available.")
        return None
    except Exception as e:
        _app_token_retry_after = now + 60
        if not _app_token_warned:
            _app_token_warned = True
            print(f"[twitch] App token error: {e}")
        return None


def get_access_token():
    """A usable bearer token for public Helix reads — user token preferred."""
    try:
        from . import twitch_auth
        ut = twitch_auth.get_user_token()
        if ut:
            return ut
    except Exception:
        pass
    return _app_access_token()


def get_twitch_status(state):
    token = get_access_token()
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


def get_twitch_user_info(state):
    token = get_access_token()
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
    except Exception as e:
        print(f"[twitch] user info error: {e}")
