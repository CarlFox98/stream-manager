"""Channel-point redemptions → game actions.

Twitch requires that a custom reward be *created by this same client id* before
its redemptions can be queried or fulfilled via Helix. So on startup we make
sure each enabled reward exists (creating it if needed), then poll the
"UNFULFILLED" queue for each reward and route new redemptions to games.run_action.

Polling (rather than EventSub websockets) keeps this dependency-free and matches
the app's existing status-poll design; latency is just the poll interval.

Helix endpoints used (all need channel:manage:redemptions / channel:read:redemptions):
  POST   /helix/channel_points/custom_rewards
  GET    /helix/channel_points/custom_rewards?only_manageable_rewards=true
  GET    /helix/channel_points/custom_rewards/redemptions?status=UNFULFILLED
  PATCH  /helix/channel_points/custom_rewards/redemptions   (set FULFILLED/CANCELED)
"""
import json, os, threading, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

from . import games, twitch_auth
from .config import BASE_DIR, TWITCH_CLIENT_ID, config

_BASE = "https://api.twitch.tv/helix/channel_points/custom_rewards"
_IDS_FILE = os.path.join(BASE_DIR, "data", "redeem_ids.json")

status = {
    "ready": False,
    "error": "",
    "polls": 0,
    "rewards": {},   # action -> {"title","id","cost","enabled"}
    "transport": "polling",   # "eventsub" once the websocket takes over
}

_stop = threading.Event()
_seen = set()             # redemption ids handled this session (belt-and-suspenders)
_reward_action = {}       # reward_id -> action (lets EventSub route by reward)
_STARTED_AT = time.time()  # redemptions older than this are backlog

# Which game each redeem action maps to (matches games.NAMED_ACTIONS).
_ACTIONS = ("lucky", "risky", "coinflip", "5050")

# Each redeem also carries Twitch-native limits, enforced by Twitch *before* the
# viewer's points are spent:
#   global_cooldown            seconds nobody can redeem again (0 = off)
#   max_per_user_per_stream    per-viewer cap each stream (0 = unlimited)
#   max_per_stream             total cap each stream (0 = unlimited)
DEFAULT_REDEEMS = {
    "lucky":    {"title": "Lucky Wheel Spin", "cost": 500, "enabled": True,
                 "prompt": "Spin the Lucky Wheel for a fun reward!",
                 "global_cooldown": 60, "max_per_user_per_stream": 3, "max_per_stream": 0},
    "risky":    {"title": "Risky Wheel Spin", "cost": 500, "enabled": True,
                 "prompt": "Spin the Risky Wheel… if you dare.",
                 "global_cooldown": 60, "max_per_user_per_stream": 3, "max_per_stream": 0},
    "coinflip": {"title": "Coin Flip", "cost": 100, "enabled": False,
                 "prompt": "Flip a coin — heads or tails?",
                 "global_cooldown": 15, "max_per_user_per_stream": 0, "max_per_stream": 0},
    "5050":     {"title": "50/50", "cost": 100, "enabled": False,
                 "prompt": "Take a 50/50 gamble.",
                 "global_cooldown": 15, "max_per_user_per_stream": 0, "max_per_stream": 0},
}


def _redeem_cfg():
    cfg = dict(DEFAULT_REDEEMS)
    for k, v in (config.get("redeems") or {}).items():
        if k in cfg and isinstance(v, dict):
            cfg[k] = {**cfg[k], **v}
    return cfg


def _headers():
    token = twitch_auth.get_user_token()
    if not token:
        return None
    return {"Client-ID": TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"}


def _request(method, url, body=None):
    """Return (status_code, parsed_json). None headers → (0, {})."""
    h = _headers()
    if not h:
        return 0, {}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=h, method=method)
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


def _broadcaster_id():
    return twitch_auth.auth.get("user_id", "")


# ── reward setup ──────────────────────────────────────────────────────────
def _load_ids():
    try:
        with open(_IDS_FILE, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_ids(mapping):
    try:
        os.makedirs(os.path.dirname(_IDS_FILE), exist_ok=True)
        with open(_IDS_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f, indent=2)
    except Exception as e:
        print(f"[redeems] could not save reward ids: {e}")


def _list_manageable():
    """Return ({title_lower: id}, {id: reward_dict}) for manageable rewards."""
    bid = _broadcaster_id()
    if not bid:
        return {}, {}
    q = urllib.parse.urlencode({"broadcaster_id": bid, "only_manageable_rewards": "true"})
    code, body = _request("GET", f"{_BASE}?{q}")
    by_title, by_id = {}, {}
    if code == 200:
        for r in body.get("data", []):
            by_title[r["title"].strip().lower()] = r["id"]
            by_id[r["id"]] = r
    return by_title, by_id


def _reward_limits(r):
    """Twitch-native limit fields from a redeem config block."""
    gc = int(r.get("global_cooldown", 0) or 0)
    mpu = int(r.get("max_per_user_per_stream", 0) or 0)
    mps = int(r.get("max_per_stream", 0) or 0)
    return {
        "is_global_cooldown_enabled": gc > 0,
        "global_cooldown_seconds": max(gc, 1),          # Twitch requires >=1 when enabled
        "is_max_per_user_per_stream_enabled": mpu > 0,
        "max_per_user_per_stream": max(mpu, 1),
        "is_max_per_stream_enabled": mps > 0,
        "max_per_stream": max(mps, 1),
    }


def _create_reward(r):
    bid = _broadcaster_id()
    q = urllib.parse.urlencode({"broadcaster_id": bid})
    payload = {
        "title": r["title"],
        "cost": int(max(r.get("cost", 1), 1)),
        "prompt": r.get("prompt", "") or "",
        "is_user_input_required": False,
        "should_redemptions_skip_request_queue": False,  # keep queue so we can fulfil
        **_reward_limits(r),
    }
    code, body = _request("POST", f"{_BASE}?{q}", payload)
    if code in (200, 201) and body.get("data"):
        return body["data"][0]["id"]
    # 400 usually = a reward with this title already exists (created earlier).
    return None


def _update_reward(reward_id, r):
    """Sync cost/prompt/limits onto an already-existing reward (idempotent)."""
    bid = _broadcaster_id()
    q = urllib.parse.urlencode({"broadcaster_id": bid, "id": reward_id})
    payload = {
        "title": r["title"],
        "cost": int(max(r.get("cost", 1), 1)),
        "prompt": r.get("prompt", "") or "",
        **_reward_limits(r),
    }
    _request("PATCH", f"{_BASE}?{q}", payload)


def ensure_rewards():
    """Create/reuse each enabled reward; populate status['rewards']. Returns True.

    Reward IDs are remembered in data/redeem_ids.json and matched by ID first,
    so renaming a reward in the Twitch dashboard won't spawn a duplicate. Falls
    back to matching by title, then creates.
    """
    if not _broadcaster_id():
        status["error"] = "not authorized"
        return False
    by_title, by_id = _list_manageable()
    saved = _load_ids()
    redeems = _redeem_cfg()
    _reward_action.clear()
    ok_any = False
    for action in _ACTIONS:
        r = redeems[action]
        entry = {"title": r["title"], "id": "", "cost": r["cost"], "enabled": bool(r["enabled"])}
        if r["enabled"]:
            # 1) saved id that still exists  2) match by title  3) create
            rid = saved.get(action) if saved.get(action) in by_id else None
            rid = rid or by_title.get(r["title"].strip().lower())
            if rid:
                _update_reward(rid, r)      # keep title/limits/cost in sync with config
            else:
                rid = _create_reward(r)     # create with limits already applied
            if rid:
                entry["id"] = rid
                saved[action] = rid
                _reward_action[rid] = action
                ok_any = True
            else:
                status["error"] = f"could not create/find reward '{r['title']}'"
        status["rewards"][action] = entry
    _save_ids(saved)
    status["ready"] = ok_any
    if ok_any:
        status["error"] = ""
    return ok_any


# ── redemption handling (shared by the poller and EventSub) ────────────────
def _fulfill(reward_id, redemption_id, new_status="FULFILLED"):
    bid = _broadcaster_id()
    q = urllib.parse.urlencode({"broadcaster_id": bid, "reward_id": reward_id, "id": redemption_id})
    _request("PATCH", f"{_BASE}/redemptions?{q}", {"status": new_status})


def _redeem_opts():
    r = config.get("redeems") if isinstance(config.get("redeems"), dict) else {}
    return {
        "auto_fulfill": r.get("auto_fulfill", True),
        "refund_on_failure": r.get("refund_on_failure", False),
        "catch_up": r.get("catch_up", False),   # process redemptions from before startup?
    }


def _too_old(red):
    """True if this redemption happened before the app started (backlog)."""
    ts = red.get("redeemed_at")
    if not ts:
        return False
    try:
        when = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return False
    return when < _STARTED_AT - 2   # small grace window


def handle_redemption(reward_id, red, action=None):
    """Route one redemption to its game, then fulfil or refund. Idempotent.

    Called by both the poller and the EventSub client. `red` is the Twitch
    redemption dict (needs id, user_name, redeemed_at).
    """
    from . import chat  # late import: chat.say is the reply channel
    rid = red.get("id")
    if not rid or rid in _seen:
        return
    action = action or _reward_action.get(reward_id)
    if not action:
        return
    opts = _redeem_opts()
    if _too_old(red) and not opts["catch_up"]:
        _seen.add(rid)
        return  # ignore backlog from before we started (no refund, just skip)
    _seen.add(rid)
    user = red.get("user_name") or red.get("user_login") or "someone"

    ok = False
    try:
        ok = games.run_action(action, user=user, say=chat.say) is not None
    except Exception as e:
        print(f"[redeems] handler error for {action}: {e}")
        ok = False

    if not ok and opts["refund_on_failure"]:
        _fulfill(reward_id, rid, "CANCELED")     # refund the viewer's points
    elif opts["auto_fulfill"]:
        _fulfill(reward_id, rid, "FULFILLED")


def _poll_reward(action, reward_id):
    bid = _broadcaster_id()
    q = urllib.parse.urlencode({
        "broadcaster_id": bid, "reward_id": reward_id,
        "status": "UNFULFILLED", "sort": "OLDEST", "first": 50,
    })
    code, body = _request("GET", f"{_BASE}/redemptions?{q}")
    if code != 200:
        if code in (401, 403):
            status["error"] = f"redemptions read denied ({code}) — check scopes/affiliate"
        return
    for red in body.get("data", []):
        handle_redemption(reward_id, red, action)


def _loop():
    # First, make sure rewards exist. Retry a few times if auth isn't ready yet.
    for _ in range(30):
        if _stop.is_set():
            return
        if twitch_auth.get_user_token() and _broadcaster_id():
            ensure_rewards()
            break
        time.sleep(2)

    interval = max(int(config.get("redeem_poll_interval", 3)), 2)
    while not _stop.is_set():
        if status.get("ready"):
            # If EventSub is driving redemptions, don't also poll (avoid dupes).
            if status.get("transport") != "eventsub":
                for action, info in list(status["rewards"].items()):
                    if info.get("id"):
                        try:
                            _poll_reward(action, info["id"])
                        except Exception as e:
                            status["error"] = str(e)
                status["polls"] += 1
        else:
            # auth may have completed after startup — try to set up rewards
            if twitch_auth.get_user_token() and _broadcaster_id():
                ensure_rewards()
        # trim the seen-set so it can't grow without bound
        if len(_seen) > 2000:
            _seen.clear()
        time.sleep(interval)


def start():
    if getattr(start, "_thread", None) and start._thread.is_alive():
        return
    _stop.clear()
    start._thread = threading.Thread(target=_loop, daemon=True)
    start._thread.start()


def stop():
    _stop.set()


def public_status():
    return {
        "ready": status["ready"],
        "error": status["error"],
        "polls": status["polls"],
        "rewards": status["rewards"],
        "transport": status["transport"],
    }
