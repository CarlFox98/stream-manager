"""Automated outcomes for wheel segments — a small, *allowlisted* action runner.

A wheel segment may carry an `"action"` string. When automation is enabled and
the spin has a real target viewer, that action actually changes Twitch state
instead of only announcing. Supported actions (anything else is ignored):

    scene:<set>        switch the overlay scene set (e.g. "scene:retro")
    vip                grant the redeeming viewer VIP
    shoutout           /shoutout the redeeming viewer (or shoutout:<login>)
    timeout:<seconds>  time the redeeming viewer out (self-inflicted; risky wheel)

Deliberately NO "mod" action — handing out moderator powers automatically is a
security risk. Automation is opt-in via config → automation.enabled, and
per-action toggles let you disable e.g. timeouts without editing the wheel.

Helix scopes needed: channel:manage:vips, moderator:manage:banned_users,
moderator:manage:shoutouts (plus the base chat/redemption scopes).
"""
import json, urllib.error, urllib.parse, urllib.request

from . import twitch_auth
from .config import TWITCH_CLIENT_ID, config

_HELIX = "https://api.twitch.tv/helix"
ALLOWED = ("scene", "vip", "shoutout", "timeout")


def _opts():
    a = config.get("automation") if isinstance(config.get("automation"), dict) else {}
    return {
        "enabled": bool(a.get("enabled", False)),
        "allow_scene": a.get("allow_scene", True),
        "allow_vip": a.get("allow_vip", True),
        "allow_shoutout": a.get("allow_shoutout", True),
        "allow_timeout": a.get("allow_timeout", True),
    }


def enabled():
    return _opts()["enabled"]


def _helix(method, path, params=None, body=None):
    token = twitch_auth.get_user_token()
    if not token:
        return 0, {}
    url = f"{_HELIX}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Client-ID": TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"_error": str(e)}


def _bid():
    return twitch_auth.auth.get("user_id", "")


def _resolve_login(login):
    """Login name -> user id (for shoutout:<login>)."""
    code, body = _helix("GET", "users", params={"login": login})
    if code == 200 and body.get("data"):
        return body["data"][0]["id"]
    return ""


# ── individual actions ─────────────────────────────────────────────────────
def _do_scene(set_name, say):
    from . import scenes
    ok, msg = scenes.apply_scene_set(set_name)
    print(f"[actions] scene→{set_name}: {msg}")
    return ok


def _do_vip(user, user_id, say):
    if not user_id:
        return False
    code, _ = _helix("POST", "channels/vips",
                     params={"broadcaster_id": _bid(), "user_id": user_id})
    ok = code in (200, 204)
    if ok and say:
        say(f"⭐ {user} is now a VIP!")
    elif code == 409 and say:      # already a VIP
        say(f"⭐ {user} is already a VIP!")
    return ok or code == 409


def _do_shoutout(login_or_user, user_id, say):
    to_id = user_id
    if not to_id and login_or_user:
        to_id = _resolve_login(login_or_user)
    if not to_id:
        return False
    code, _ = _helix("POST", "chat/shoutouts", params={
        "from_broadcaster_id": _bid(), "to_broadcaster_id": to_id, "moderator_id": _bid(),
    })
    ok = code in (200, 204)
    if ok and say:
        say(f"📣 Shoutout to {login_or_user}! Go give them a follow.")
    return ok


def _do_timeout(user, user_id, seconds, say):
    if not user_id:
        return False
    try:
        dur = max(1, min(int(seconds or 60), 1209600))   # Twitch max 14 days
    except (ValueError, TypeError):
        dur = 60
    code, _ = _helix("POST", "moderation/bans",
                     params={"broadcaster_id": _bid(), "moderator_id": _bid()},
                     body={"data": {"user_id": user_id, "duration": dur,
                                    "reason": "Risky Wheel outcome"}})
    ok = code in (200, 204)
    if ok and say:
        say(f"⏱️ {user} got a {dur}s timeout from the Risky Wheel!")
    return ok


# ── entry point ────────────────────────────────────────────────────────────
def run(spec, user="", user_id="", say=None):
    """Execute an allowlisted action spec. No-op unless automation is enabled.

    Returns True if the action ran (or was harmlessly already-applied).
    """
    if not spec or not enabled():
        return False
    typ, _, arg = str(spec).partition(":")
    typ = typ.strip().lower()
    arg = arg.strip()
    if typ not in ALLOWED:
        print(f"[actions] ignoring unknown action '{spec}'")
        return False
    o = _opts()
    try:
        if typ == "scene" and o["allow_scene"]:
            return _do_scene(arg, say)
        if typ == "vip" and o["allow_vip"]:
            return _do_vip(user, user_id, say)
        if typ == "shoutout" and o["allow_shoutout"]:
            return _do_shoutout(arg or user, "" if arg else user_id, say)
        if typ == "timeout" and o["allow_timeout"]:
            return _do_timeout(user, user_id, arg or 60, say)
    except Exception as e:
        print(f"[actions] '{spec}' failed: {e}")
    return False
