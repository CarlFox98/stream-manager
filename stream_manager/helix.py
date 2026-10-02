"""One shared Helix request helper for the broadcaster's *user* token.

actions.py, the wheel outcomes and the reward pauser all talk to Helix as the
broadcaster. They used to carry their own copies of this function; a fix to one
copy (timeouts, error shapes) never reached the others.

request() never raises. It returns (status, body): status 0 means "no token" or
a transport failure, and body["_error"] carries the reason in the latter case.
"""
import json, urllib.error, urllib.parse, urllib.request

from . import twitch_auth
from .config import TWITCH_CLIENT_ID

BASE = "https://api.twitch.tv/helix"


def request(method, path, params=None, body=None, timeout=10):
    token = twitch_auth.get_user_token()
    if not token:
        return 0, {}
    url = f"{BASE}/{path.lstrip('/')}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Client-ID": TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"_error": str(e)}


def broadcaster_id():
    return twitch_auth.auth.get("user_id", "")


def ok(code):
    return code in (200, 201, 202, 204)


def error_text(code, body):
    """Short human reason for a failed call, for chat and the dashboard."""
    if code == 0:
        return (body or {}).get("_error") or "not authorized"
    msg = (body or {}).get("message") or ""
    if code in (401, 403):
        return f"Twitch refused ({code}) — re-authorize on the dashboard{': ' + msg if msg else ''}"
    return f"Twitch error {code}{': ' + msg if msg else ''}"
