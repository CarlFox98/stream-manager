"""Interactive game logic: coinflip, 50/50, and the Lucky / Risky wheels,
plus the chat-command dispatcher that drives them and the quote book.

Everything here is transport-agnostic. A `say` callback delivers chat replies
(supplied by chat.py, the redemption poller, or the dashboard test route), and
overlay animations are fired via effects.emit(). That keeps this module easy to
unit-test without a live Twitch connection.
"""
import json, os, random, threading, time

from . import cooldowns, effects, quotes, stats
from .config import BASE_DIR, config

_SPIN_FILE = os.path.join(BASE_DIR, "data", "spin_history.json")

# ── cooldowns (chat-command path only; redeems are limited by Twitch) ──────
_DEFAULT_COOLDOWNS = {
    "mods_bypass": True,
    "coinflip": {"user": 30, "global": 3},
    "5050":     {"user": 30, "global": 3},
    "slots":    {"user": 30, "global": 3},
    "dice":     {"user": 10, "global": 0},
    "duel":     {"user": 30, "global": 3},
    "lucky":    {"user": 60, "global": 5},
    "risky":    {"user": 60, "global": 5},
    "quote":    {"user": 10, "global": 0},
}


def _cooldown_cfg(action):
    base = _DEFAULT_COOLDOWNS.get(action, {})
    user_cfg = (config.get("cooldowns") or {}).get(action, {})
    return {**base, **(user_cfg if isinstance(user_cfg, dict) else {})}


def _gate(action, user, can_edit, say):
    """Cooldown gate for a chat command. Returns True if it may run now."""
    if can_edit and (config.get("cooldowns") or {}).get("mods_bypass",
                                                         _DEFAULT_COOLDOWNS["mods_bypass"]):
        return True
    cd = _cooldown_cfg(action)
    ok, wait, scope = cooldowns.check(action, user, cd.get("user", 0), cd.get("global", 0))
    if not ok and say and cooldowns.should_notify(action, user):
        secs = int(wait) + 1
        if scope == "global":
            say(f"⏳ {action} is cooling down — try again in {secs}s")
        else:
            say(f"⏳ @{user} you can use {action} again in {secs}s")
    return ok


# ── config helpers ────────────────────────────────────────────────────────
def _wheel_cfg(kind):
    """Return the config block for 'lucky' or 'risky', with sane fallbacks."""
    wheels = config.get("wheels") or {}
    w = wheels.get(kind) or {}
    segs = w.get("segments") or []
    if not segs:
        segs = _DEFAULT_WHEELS[kind]["segments"]
    return {
        "title": w.get("title") or _DEFAULT_WHEELS[kind]["title"],
        "color": w.get("color") or _DEFAULT_WHEELS[kind]["color"],
        "segments": segs,
    }


def _pick_weighted(segments, candidates=None):
    """Weighted index pick over `segments`, restricted to `candidates` indices.

    `candidates` (a list of indices) lets callers exclude some slots — e.g. a
    slot the viewer just landed on, or a 'severe' slot they're cooling down from
    — while the winning index still refers to the full segment list.
    """
    idxs = list(range(len(segments))) if candidates is None else list(candidates)
    if not idxs:
        idxs = list(range(len(segments)))
    weights = [max(float(segments[i].get("weight", 1)), 0) for i in idxs]
    total = sum(weights)
    if total <= 0:
        return random.choice(idxs)
    r = random.uniform(0, total)
    upto = 0.0
    for i, w in zip(idxs, weights):
        upto += w
        if r <= upto:
            return i
    return idxs[-1]


# ── per-viewer anti-spam for the wheels ────────────────────────────────────
# Remembers each viewer's last slot per wheel so they can't land on the exact
# same slot back-to-back, and enforces a cooldown on "severe" slots (marked
# "severe": true in config, e.g. the timeout) so one person can't be hit by the
# harsh outcome repeatedly.
_spin_history = {}         # (kind, user_lower) -> {"last_index", "last_severe_ts"}
_spin_lock = threading.Lock()


def save_spin_history():
    """Persist per-viewer spin history so anti-spam windows survive a restart."""
    try:
        os.makedirs(os.path.dirname(_SPIN_FILE), exist_ok=True)
        with _spin_lock:
            data = {f"{k}|{u}": v for (k, u), v in _spin_history.items()}
        with open(_SPIN_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[games] spin-history save failed: {e}")


def load_spin_history():
    if not os.path.isfile(_SPIN_FILE):
        return
    try:
        with open(_SPIN_FILE, encoding="utf-8") as f:
            d = json.load(f)
        with _spin_lock:
            for key, val in d.items():
                if "|" in key and isinstance(val, dict):
                    kind, u = key.split("|", 1)
                    _spin_history[(kind, u)] = val
    except Exception as e:
        print(f"[games] spin-history load failed: {e}")


def _wheel_meta(kind):
    w = (config.get("wheels") or {}).get(kind, {})
    d = _DEFAULT_WHEELS.get(kind, {})
    return {
        "no_repeat": w.get("no_repeat", d.get("no_repeat", True)),
        "severe_cooldown": int(w.get("severe_cooldown_seconds",
                                      d.get("severe_cooldown_seconds", 0)) or 0),
    }


def _pick_wheel(kind, segments, user):
    """Weighted pick with no-repeat + severe-cooldown guards for one viewer."""
    now = time.time()
    ul = (user or "").lower()
    meta = _wheel_meta(kind)
    with _spin_lock:
        hist = dict(_spin_history.get((kind, ul), {}))

    candidates = list(range(len(segments)))
    # 1) if this viewer is on severe cooldown, drop severe slots (if any remain)
    if ul and meta["severe_cooldown"] > 0 and \
            now - hist.get("last_severe_ts", 0) < meta["severe_cooldown"]:
        non_severe = [i for i in candidates if not segments[i].get("severe")]
        if non_severe:
            candidates = non_severe
    # 2) avoid the exact slot they just got (only if there's an alternative)
    if ul and meta["no_repeat"] and hist.get("last_index") in candidates and len(candidates) > 1:
        candidates = [i for i in candidates if i != hist["last_index"]]

    idx = _pick_weighted(segments, candidates)

    if ul:
        with _spin_lock:
            _spin_history[(kind, ul)] = {
                "last_index": idx,
                "last_severe_ts": now if segments[idx].get("severe") else hist.get("last_severe_ts", 0),
            }
    return idx


# PRISM scene-set palette (matches overlays/PRISM/prism-theme.css)
_PALETTE = ["#57F2E4", "#6C8BFF", "#B983FF", "#FF7ACb", "#FFD86B",
            "#7DE8FF", "#9E7BFF", "#FF9EDB", "#7CFBE0", "#8AA6FF"]


def _colored(segments):
    """Ensure every segment has a display color (cycle the palette otherwise)."""
    out = []
    for i, s in enumerate(segments):
        out.append({"label": s.get("label", f"Slot {i+1}"),
                    "color": s.get("color") or _PALETTE[i % len(_PALETTE)]})
    return out


# ── games ─────────────────────────────────────────────────────────────────
def coinflip(user, say=None):
    result = random.choice(["HEADS", "TAILS"])
    effects.emit("coinflip", {"type": "coin", "result": result, "user": user},
                 summary=f"{user or 'Someone'} flipped {result}")
    stats.record("coinflip", "coinflip", user, result)
    if say:
        say(f"🪙 {user} flipped… {result}!")
    return result


def fifty_fifty(user, say=None):
    win = random.random() < 0.5
    result = "WIN" if win else "LOSE"
    effects.emit("coinflip", {"type": "5050", "result": result, "win": win, "user": user},
                 summary=f"{user or 'Someone'} rolled 50/50 → {result}")
    stats.record("5050", "5050", user, result)
    if say:
        say(f"🎲 50/50 for {user}… {'✅ WIN!' if win else '❌ LOSE!'}")
    return result


def spin_wheel(kind, user, say=None, user_id=""):
    """Spin the 'lucky' or 'risky' wheel. Returns the winning label."""
    cfg = _wheel_cfg(kind)
    segments = _colored(cfg["segments"])
    idx = _pick_wheel(kind, cfg["segments"], user)   # weighted + anti-spam guards
    winseg = cfg["segments"][idx]
    winner = segments[idx]["label"]
    effects.emit("wheel", {
        "mode": kind,
        "title": cfg["title"],
        "color": cfg["color"],
        "segments": segments,
        "winner_index": idx,
        "winner": winner,
        "user": user,
    }, summary=f"{cfg['title']}: {user or 'Someone'} → {winner}")
    stats.record("wheel", kind, user, winner)
    if say:
        icon = "🍀" if kind == "lucky" else "☠️"
        say(f"{icon} {cfg['title']} — {user} landed on: {winner}")
    # optional automated outcome (opt-in + allowlisted; needs a real target)
    spec = winseg.get("action")
    if spec:
        from . import actions
        actions.run(spec, user=user, user_id=user_id, say=say)
    return winner


# ── more games ─────────────────────────────────────────────────────────────
_SLOT_SYMBOLS = ["🍒", "🍋", "🔔", "⭐", "🦊", "💎", "7️⃣"]


def slots(user, say=None, user_id=""):
    reels = [random.choice(_SLOT_SYMBOLS) for _ in range(3)]
    win = reels[0] == reels[1] == reels[2]
    near = (not win) and len(set(reels)) == 2
    effects.emit("slots", {"reels": reels, "win": win, "user": user},
                 summary=f"{user or 'Someone'} spun {' '.join(reels)}" + (" — JACKPOT" if win else ""))
    stats.record("slots", "slots", user, "JACKPOT" if win else "".join(reels))
    if say:
        if win:
            say(f"🎰 {user} — {' '.join(reels)} — JACKPOT! 🎉")
        elif near:
            say(f"🎰 {user} — {' '.join(reels)} — so close!")
        else:
            say(f"🎰 {user} — {' '.join(reels)} — no luck this time!")
    return "".join(reels)


def dice(user, say=None, sides=6):
    try:
        sides = max(2, min(int(sides), 1000))
    except (ValueError, TypeError):
        sides = 6
    roll = random.randint(1, sides)
    stats.record("dice", "dice", user, str(roll))
    if say:
        say(f"🎲 {user} rolled a {roll} (d{sides})")
    return roll


_EIGHT_BALL = [
    "It is certain.", "Without a doubt.", "Yes — definitely.", "Most likely.",
    "Signs point to yes.", "Reply hazy, try again.", "Ask again later.",
    "Better not tell you now.", "Cannot predict now.", "Don't count on it.",
    "My reply is no.", "Very doubtful.", "Outlook not so good.", "Absolutely not.",
]


def eight_ball(question, user, say=None):
    ans = random.choice(_EIGHT_BALL)
    stats.record("8ball", "8ball", user, ans)
    if say:
        say(f"🎱 {user}: {ans}")
    return ans


def duel(challenger, target, say=None):
    target = (target or "").lstrip("@").strip()
    if not target:
        if say:
            say("⚔️ Usage: !duel @username")
        return None
    winner = random.choice([challenger, target])
    loser = target if winner == challenger else challenger
    stats.record("duel", "duel", winner, f"beat {loser}")
    flavor = random.choice([
        "lands a critical hit on", "out-maneuvers", "body-slams",
        "pixel-blasts", "clutches the win against", "flawless-victories",
    ])
    if say:
        say(f"⚔️ {winner} {flavor} {loser}! 🏆")
    return winner


# ── quote commands ────────────────────────────────────────────────────────
def _handle_quote(args, user, can_edit, say):
    if not args:
        q = quotes.random_quote()
        say(quotes.format_quote(q) if q else "No quotes yet. Add one with !addquote <text>")
        return
    sub = args[0].lower()
    if sub in ("add",) and can_edit:
        text = " ".join(args[1:]).strip()
        q = quotes.add(text, added_by=user)
        say(f"Added quote #{q['id']}." if q else "Usage: !addquote <text>")
        return
    if sub in ("del", "delete", "remove") and can_edit and len(args) > 1 and args[1].isdigit():
        ok = quotes.delete(int(args[1]))
        say(f"Deleted quote #{args[1]}." if ok else f"No quote #{args[1]}.")
        return
    if sub in ("count",):
        say(f"There are {quotes.count()} quotes.")
        return
    if args[0].isdigit():
        q = quotes.get(int(args[0]))
        say(quotes.format_quote(q) if q else f"No quote #{args[0]}.")
        return
    say("Usage: !quote [number] · !addquote <text> · !delquote <number> · !quote count")


# ── dispatch ──────────────────────────────────────────────────────────────
# Map canonical action names (redeems / dashboard tests) to a common
# (user, say, user_id) signature.
NAMED_ACTIONS = {
    "coinflip": lambda user, say, user_id: coinflip(user, say),
    "5050":     lambda user, say, user_id: fifty_fifty(user, say),
    "lucky":    lambda user, say, user_id: spin_wheel("lucky", user, say, user_id),
    "risky":    lambda user, say, user_id: spin_wheel("risky", user, say, user_id),
    "slots":    lambda user, say, user_id: slots(user, say, user_id),
}


def run_action(name, user="", say=None, user_id=""):
    """Trigger a game by canonical name (used by redemptions + dashboard tests)."""
    fn = NAMED_ACTIONS.get(name)
    if not fn:
        return None
    return fn(user, say, user_id)


def handle_command(message, user, is_mod=False, is_broadcaster=False, say=None,
                   prefix="!", user_id=""):
    """Parse one chat line. Returns True if it was a recognized command.

    `say(text)` sends a chat reply; may be None (fire-and-forget overlays only).
    """
    say = say or (lambda _t: None)
    if not message.startswith(prefix):
        return False
    parts = message[len(prefix):].strip().split()
    if not parts:
        return False
    cmd = parts[0].lower()
    args = parts[1:]
    can_edit = is_mod or is_broadcaster

    if cmd in ("coinflip", "flip", "coin"):
        if _gate("coinflip", user, can_edit, say):
            coinflip(user, say)
        return True
    if cmd in ("5050", "fiftyfifty", "50/50"):
        if _gate("5050", user, can_edit, say):
            fifty_fifty(user, say)
        return True
    if cmd in ("slots", "slot"):
        if _gate("slots", user, can_edit, say):
            slots(user, say, user_id)
        return True
    if cmd in ("dice", "roll"):
        if _gate("dice", user, can_edit, say):
            dice(user, say, args[0] if args else 6)
        return True
    if cmd in ("8ball", "eightball"):
        eight_ball(" ".join(args), user, say); return True
    if cmd in ("duel", "fight"):
        if _gate("duel", user, can_edit, say):
            duel(user, args[0] if args else "", say)
        return True
    if cmd in ("luckywheel", "luckyspin", "lucky") and can_edit:
        spin_wheel("lucky", user, say, user_id); return True
    if cmd in ("riskywheel", "riskyspin", "risky") and can_edit:
        spin_wheel("risky", user, say, user_id); return True
    if cmd in ("quote",):
        # only rate-limit plain lookups, not mod add/del sub-commands
        is_lookup = not (args and args[0].lower() in ("add", "del", "delete", "remove"))
        if not is_lookup or _gate("quote", user, can_edit, say):
            _handle_quote(args, user, can_edit, say)
        return True
    if cmd in ("addquote",):
        q = quotes.add(" ".join(args), added_by=user) if can_edit else None
        say(f"Added quote #{q['id']}." if q else ("Usage: !addquote <text>" if can_edit else "Mods only.")); return True
    if cmd in ("delquote",):
        if can_edit and args and args[0].isdigit():
            ok = quotes.delete(int(args[0]))
            say(f"Deleted quote #{args[0]}." if ok else f"No quote #{args[0]}.")
        else:
            say("Usage: !delquote <number> (mods only)")
        return True
    if cmd in ("quotecount",):
        say(f"There are {quotes.count()} quotes."); return True
    if cmd in ("commands", "help"):
        say(f"Games: {prefix}coinflip · {prefix}5050 · {prefix}slots · {prefix}dice · "
            f"{prefix}8ball <q> · {prefix}duel @user · {prefix}quote [n] · {prefix}addquote (mods)")
        return True
    return False


# ── default wheel content (used until you customize config.json) ───────────
_DEFAULT_WHEELS = {
    "lucky": {
        "title": "Lucky Wheel",
        "color": "#57F2E4",
        "no_repeat": True,             # never the same slot twice in a row per viewer
        # NOTE: deliberately no "temp mod" / privilege-escalation rewards here.
        # Handing out moderator powers for cheap channel points is a security
        # risk (mods can ban, delete messages, edit the stream, run commands).
        # If you ever add one, gate it behind a very high cost + tiny weight,
        # and remember the app only *announces* — you grant it yourself.
        "segments": [
            {"label": "Show your sona on the starting screen 🦊", "weight": 3},
            {"label": "VIP for a week", "weight": 1, "action": "vip"},
            {"label": "Pick the next song 🎵", "weight": 3},
            {"label": "Choose the next game", "weight": 2},
            {"label": "Streamer does 10 push-ups", "weight": 2},
            {"label": "Add your emote suggestion to the list 😸", "weight": 2},
            {"label": "Shoutout + follow", "weight": 3, "action": "shoutout"},
            {"label": "JACKPOT: all of the above 🎉", "weight": 1},
        ],
    },
    "risky": {
        "title": "Risky Wheel",
        "color": "#FF7ACb",
        "no_repeat": True,             # never the same slot twice in a row per viewer
        "severe_cooldown_seconds": 900,  # a viewer can't hit a "severe" slot again for 15 min
        "segments": [
            {"label": "Read a bad pun on stream 😹", "weight": 3},
            {"label": "Play one round blindfolded", "weight": 2},
            {"label": "Swap to the cursed overlay theme", "weight": 2},
            {"label": "Talk in a silly voice for 5 min", "weight": 3},
            {"label": "Timeout for 60s ⏱️", "weight": 1, "severe": True, "action": "timeout:60"},
            {"label": "Nothing happens… this time 😈", "weight": 3},
            {"label": "Let chat rename your sona (1 stream)", "weight": 1},
            {"label": "Do the next challenge on hard mode", "weight": 2},
        ],
    },
}
