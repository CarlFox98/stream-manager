"""EventSub over WebSocket — near-instant redemptions + raid/bits/sub hype.

This is an optional upgrade over the redemption *polling* in redeems.py. When
the `websocket-client` package is installed (already an optional dependency for
OBS status) and the user is authorized, we open Twitch's EventSub websocket,
subscribe to the events we care about, and route them:

  channel.channel_points_custom_reward_redemption.add → redeems.handle_redemption
  channel.raid    → hype overlay (+ optional free wheel spin)
  channel.cheer   → hype overlay
  channel.subscribe → hype overlay

If websocket-client isn't present, or the socket drops, redeems.py keeps
polling, so redemptions always work either way. While EventSub is connected it
sets redeems.status['transport'] = 'eventsub' so the poller stands down.

Docs: https://dev.twitch.tv/docs/eventsub/handling-websocket-events/
"""
import json, threading, time, urllib.error, urllib.parse, urllib.request

from . import effects, games, redeems, twitch_auth
from .config import TWITCH_CLIENT_ID, config

try:
    import websocket  # from websocket-client
    _HAVE_WS = True
except Exception:
    _HAVE_WS = False

_WS_URL = "wss://eventsub.wss.twitch.tv/ws"
_SUB_URL = "https://api.twitch.tv/helix/eventsub/subscriptions"

status = {"connected": False, "error": "", "subs": 0, "available": _HAVE_WS}
_stop = threading.Event()
_seen_msg = set()   # EventSub message ids (dedupe per Twitch guidance)


def available():
    return _HAVE_WS


def _opts():
    e = config.get("eventsub") if isinstance(config.get("eventsub"), dict) else {}
    return {
        "enabled": e.get("enabled", True),
        "raid_free_spin": e.get("raid_free_spin", False),
        "raid": e.get("raid", True),
        "cheer": e.get("cheer", True),
        "subscribe": e.get("subscribe", True),
    }


def _subscribe(session_id):
    """Create the websocket-transport subscriptions. Returns count created."""
    bid = twitch_auth.auth.get("user_id", "")
    token = twitch_auth.get_user_token()
    if not bid or not token:
        return 0
    o = _opts()
    subs = [("channel.channel_points_custom_reward_redemption.add", "1", {"broadcaster_user_id": bid})]
    if o["raid"]:
        subs.append(("channel.raid", "1", {"to_broadcaster_user_id": bid}))
    if o["cheer"]:
        subs.append(("channel.cheer", "1", {"broadcaster_user_id": bid}))
    if o["subscribe"]:
        subs.append(("channel.subscribe", "1", {"broadcaster_user_id": bid}))

    made = 0
    for typ, ver, cond in subs:
        body = json.dumps({
            "type": typ, "version": ver, "condition": cond,
            "transport": {"method": "websocket", "session_id": session_id},
        }).encode()
        req = urllib.request.Request(_SUB_URL, data=body, method="POST", headers={
            "Client-ID": TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                if r.status in (200, 202):
                    made += 1
        except urllib.error.HTTPError as e:
            print(f"[eventsub] subscribe {typ} failed: {e.code} {e.read().decode(errors='replace')[:160]}")
        except Exception as e:
            print(f"[eventsub] subscribe {typ} error: {e}")
    return made


# ── event routing ──────────────────────────────────────────────────────────
def _on_notification(payload):
    sub = payload.get("subscription", {})
    event = payload.get("event", {})
    typ = sub.get("type", "")
    if typ == "channel.channel_points_custom_reward_redemption.add":
        reward_id = (event.get("reward") or {}).get("id", "")
        red = {
            "id": event.get("id"),
            "user_name": event.get("user_name") or event.get("user_login"),
            "user_id": event.get("user_id"),
            "redeemed_at": event.get("redeemed_at"),
            "user_input": event.get("user_input", ""),
        }
        redeems.handle_redemption(reward_id, red)
    elif typ == "channel.raid":
        _hype("raid", event.get("from_broadcaster_user_name", "Someone"),
              event.get("viewers", 0))
    elif typ == "channel.cheer":
        _hype("bits", event.get("user_name") or "Anonymous", event.get("bits", 0))
    elif typ == "channel.subscribe":
        tier = str(event.get("tier", "1000"))
        _hype("sub", event.get("user_name") or "Someone", tier)


def _hype(kind, user, amount):
    from . import chat
    labels = {"raid": "RAID", "bits": "BITS", "sub": "SUB"}
    effects.emit("hype", {"kind": kind, "user": user, "amount": amount},
                 summary=f"{labels.get(kind, kind)}: {user} ({amount})")
    try:
        if kind == "raid":
            chat.say(f"🎉 Raid! Welcome {user} and the {amount} raiders!")
            if _opts()["raid_free_spin"]:
                games.spin_wheel("lucky", user, chat.say)
        elif kind == "bits":
            chat.say(f"💎 Thanks {user} for {amount} bits!")
        elif kind == "sub":
            chat.say(f"⭐ Thanks for subscribing, {user}!")
    except Exception as e:
        print(f"[eventsub] hype reply error: {e}")


# ── connection loop ─────────────────────────────────────────────────────────
def _run_once():
    ws = websocket.create_connection(_WS_URL, timeout=15)
    ws.settimeout(15)
    keepalive = 30
    last_msg = time.time()
    try:
        while not _stop.is_set():
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                if time.time() - last_msg > keepalive + 15:
                    status["error"] = "keepalive timeout"
                    break
                continue
            if not raw:
                continue
            last_msg = time.time()
            msg = json.loads(raw)
            meta = msg.get("metadata", {})
            mid = meta.get("message_id")
            if mid and mid in _seen_msg:
                continue
            if mid:
                _seen_msg.add(mid)
                if len(_seen_msg) > 4000:
                    _seen_msg.clear()
            mtype = meta.get("message_type")
            payload = msg.get("payload", {})

            if mtype == "session_welcome":
                sid = payload.get("session", {}).get("id")
                keepalive = payload.get("session", {}).get("keepalive_timeout_seconds", 30) or 30
                made = _subscribe(sid)
                status["subs"] = made
                status["connected"] = made > 0
                if made > 0:
                    redeems.status["transport"] = "eventsub"
                    print(f"[eventsub] connected — {made} subscriptions active")
                else:
                    status["error"] = "no subscriptions created (check scopes/affiliate)"
                    break
            elif mtype == "session_keepalive":
                pass
            elif mtype == "notification":
                try:
                    _on_notification(payload)
                except Exception as e:
                    print(f"[eventsub] notification error: {e}")
            elif mtype == "session_reconnect":
                new_url = payload.get("session", {}).get("reconnect_url")
                if new_url:
                    try:
                        ws.close()
                    except Exception:
                        pass
                    ws = websocket.create_connection(new_url, timeout=15)
                    ws.settimeout(15)
            elif mtype == "revocation":
                status["error"] = "a subscription was revoked"
    finally:
        try:
            ws.close()
        except Exception:
            pass
        status["connected"] = False
        # hand redemptions back to the poller until we reconnect
        if redeems.status.get("transport") == "eventsub":
            redeems.status["transport"] = "polling"


def _loop():
    # wait for auth + rewards to be ready so subscriptions/routing work
    for _ in range(60):
        if _stop.is_set():
            return
        if twitch_auth.get_user_token() and twitch_auth.auth.get("user_id"):
            break
        time.sleep(2)
    backoff = 2
    while not _stop.is_set():
        try:
            _run_once()
        except Exception as e:
            status["error"] = str(e)
        if _stop.is_set():
            return
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)


def start():
    if not _HAVE_WS or not _opts()["enabled"]:
        return False
    if getattr(start, "_thread", None) and start._thread.is_alive():
        return True
    _stop.clear()
    start._thread = threading.Thread(target=_loop, daemon=True)
    start._thread.start()
    return True


def stop():
    _stop.set()


def public_status():
    return {"available": _HAVE_WS, "connected": status["connected"],
            "subs": status["subs"], "error": status["error"]}
