"""Viewer alerts: first-time chatters and new followers.

Both fire the PRISM **hype** overlay (kinds ``firstchat`` / ``follow``) and can
optionally post a chat greeting. Toggles and messages live in ``config.json`` →
``alerts``; leaving a message blank means "overlay only, no chat post".

    "alerts": {
      "first_chat": true,  "first_chat_message": "Welcome to the den, {user}! 👋",
      "follow_alert": true, "follow_message": ""
    }
"""
from . import effects
from .config import config

# Remember who we've already greeted this session (belt-and-suspenders on top of
# Twitch's own first-msg tag / follow de-dupe).
_greeted = set()
_followed = set()


def _cfg():
    a = config.get("alerts")
    return a if isinstance(a, dict) else {}


def _say_template(say, template, user):
    msg = (template or "").strip()
    if msg and say:
        try:
            say(msg.replace("{user}", user)[:400])
        except Exception as e:
            print(f"[alerts] chat post failed: {e}")


def first_chat(user, say=None):
    """A viewer's first message this stream (Twitch first-msg tag)."""
    if not _cfg().get("first_chat", True):
        return
    ul = (user or "").lower()
    if not ul or ul in _greeted:
        return
    _greeted.add(ul)
    effects.emit("hype", {"kind": "firstchat", "user": user}, summary=f"👋 First chat: {user}")
    _say_template(say, _cfg().get("first_chat_message"), user)


def follow(user, say=None):
    """A new follower (EventSub channel.follow)."""
    if not _cfg().get("follow_alert", True):
        return
    ul = (user or "").lower()
    if not ul or ul in _followed:
        return
    _followed.add(ul)
    if len(_followed) > 5000:
        _followed.clear()
    effects.emit("hype", {"kind": "follow", "user": user}, summary=f"💜 New follower: {user}")
    _say_template(say, _cfg().get("follow_message"), user)
