"""Twitch OAuth (app access token) + Helix status/user-info polling."""
import json, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime

from .config import TWITCH_USER, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET

_twitch_token = {"access_token": None, "expires_at": 0}


def get_access_token():
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
    except: pass
