"""Installed Steam games, read straight from the local Steam folder.

"Chat picks the next game" needs a shortlist of things you could actually
switch to right now — installed, not merely owned. Steam keeps all of that in
plain text on this PC, so no Steam account, API key or network call is needed:

  <steam>/steamapps/libraryfolders.vdf        every library folder
  <library>/steamapps/appmanifest_<id>.acf    one per installed app (name, id)
  <steam>/userdata/<uid>/config/localconfig.vdf   LastPlayed per app

Non-games (redistributables, Proton, SteamVR, wallpaper tools, soundtracks…)
are filtered by a name/appid denylist. The dashboard can pin or hide titles
(config.json → steam.pinned / steam.hidden, by appid).
"""
import os, random, re, time

from .config import config

DEFAULT_STEAM_DIRS = (
    r"C:\Program Files (x86)\Steam",
    r"C:\Program Files\Steam",
    os.path.expanduser("~/.steam/steam"),
    os.path.expanduser("~/.local/share/Steam"),
)

# appids that are never games
_DENY_IDS = {
    228980,   # Steamworks Common Redistributables
    250820,   # SteamVR
    1070560, 1391110, 1628350, 1493710, 2180100, 1887720, 961940, 1054830,  # Steam Linux Runtime / Proton
    431960,   # Wallpaper Engine
}
_DENY_NAME = re.compile(
    r"(redistributable|steamworks|proton|steam linux runtime|steamvr|soundtrack|"
    r"\bost\b|dedicated server|\bsdk\b|benchmark|wallpaper engine|artbook|"
    r"editor\b|playtest)", re.I)

_cache = {"at": 0.0, "games": None, "root": None, "error": ""}
_CACHE_SECONDS = 300


# ── tiny Valve KeyValues (VDF text) parser ─────────────────────────────────
_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def parse_vdf(text):
    """Parse VDF text into nested dicts (keys lower-cased). Lenient: unknown
    tokens are skipped and a truncated file yields whatever parsed."""
    stack = [{}]
    pending = None
    for m in _TOKEN.finditer(text):
        s, brace = m.group(1), m.group(2)
        if brace == "{":
            node = {}
            if pending is not None:
                stack[-1][pending.lower()] = node
                pending = None
            stack.append(node)
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            pending = None
        else:
            s = s.replace('\\"', '"').replace("\\\\", "\\")
            if pending is None:
                pending = s
            else:
                stack[-1][pending.lower()] = s
                pending = None
    return stack[0]


def _read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return parse_vdf(f.read())


def find_root():
    c = config.get("steam") if isinstance(config.get("steam"), dict) else {}
    for d in ([c.get("path")] if c.get("path") else []) + list(DEFAULT_STEAM_DIRS):
        if d and os.path.isfile(os.path.join(d, "steamapps", "libraryfolders.vdf")):
            return d
    return None


def _library_dirs(root):
    dirs = [root]
    try:
        lf = _read(os.path.join(root, "steamapps", "libraryfolders.vdf"))
        top = lf.get("libraryfolders") or lf
        for v in top.values():
            p = v.get("path") if isinstance(v, dict) else v if isinstance(v, str) and os.sep in v else None
            if p and os.path.isdir(p) and p not in dirs:
                dirs.append(p)
    except Exception:
        pass
    return dirs


def _last_played(root):
    """{appid: unix_ts} merged across every Steam user on this PC."""
    out = {}
    ud = os.path.join(root, "userdata")
    try:
        users = os.listdir(ud)
    except OSError:
        return out
    for u in users:
        p = os.path.join(ud, u, "config", "localconfig.vdf")
        if not os.path.isfile(p):
            continue
        try:
            d = _read(p)
            apps = (d.get("userlocalconfigstore", {}).get("software", {}).get("valve", {})
                    .get("steam", {}).get("apps", {}))
            for aid, info in apps.items():
                if isinstance(info, dict) and str(info.get("lastplayed", "0")).isdigit():
                    a = int(aid) if aid.isdigit() else None
                    if a is not None:
                        out[a] = max(out.get(a, 0), int(info["lastplayed"]))
        except Exception:
            continue
    return out


def is_game(appid, name):
    return appid not in _DENY_IDS and not _DENY_NAME.search(name or "")


def scan(root=None):
    """Installed games: [{"appid", "name", "last_played"}], newest-played first."""
    root = root or find_root()
    if not root:
        raise FileNotFoundError("Steam folder not found")
    played = _last_played(root)
    seen, games = set(), []
    for lib in _library_dirs(root):
        sa = os.path.join(lib, "steamapps")
        try:
            names = os.listdir(sa)
        except OSError:
            continue
        for fn in names:
            if not (fn.startswith("appmanifest_") and fn.endswith(".acf")):
                continue
            try:
                st = _read(os.path.join(sa, fn)).get("appstate", {})
                aid = int(st.get("appid", "0"))
                name = (st.get("name") or "").strip()
            except Exception:
                continue
            if not aid or aid in seen or not name or not is_game(aid, name):
                continue
            seen.add(aid)
            games.append({"appid": aid, "name": name, "last_played": played.get(aid, 0)})
    games.sort(key=lambda g: (-g["last_played"], g["name"].lower()))
    return games


def games(force=False):
    """Cached scan (5 min). Returns [] with _cache['error'] set on failure."""
    now = time.time()
    if not force and _cache["games"] is not None and now - _cache["at"] < _CACHE_SECONDS:
        return _cache["games"]
    try:
        g = scan()
        _cache.update(at=now, games=g, error="")
    except Exception as e:
        _cache.update(at=now, games=[], error=str(e))
    return _cache["games"]


def status():
    g = games()
    c = config.get("steam") if isinstance(config.get("steam"), dict) else {}
    return {"ok": bool(g), "error": _cache["error"], "count": len(g), "games": g,
            "pinned": c.get("pinned", []), "hidden": c.get("hidden", [])}


def short(name, limit=25):
    """Twitch poll choices are capped at 25 characters."""
    name = re.sub(r"[™®©]", "", name).strip()
    return name if len(name) <= limit else name[:limit - 1].rstrip() + "…"


def pick_choices(n=5, exclude_name="", rng=random):
    """Up to n poll choices: pinned first, then mostly recently played, plus one
    wildcard from the rest of the library. Never the game being played now."""
    c = config.get("steam") if isinstance(config.get("steam"), dict) else {}
    hidden = {int(x) for x in c.get("hidden", []) if str(x).isdigit()}
    pinned = [int(x) for x in c.get("pinned", []) if str(x).isdigit()]
    ex = (exclude_name or "").strip().lower()
    pool = [g for g in games() if g["appid"] not in hidden and g["name"].lower() != ex]
    by_id = {g["appid"]: g for g in pool}
    chosen = [by_id[a] for a in pinned if a in by_id][:n]
    rest = [g for g in pool if g not in chosen]
    recent = [g for g in rest if g["last_played"]][:max(n * 2, 8)]
    want_recent = max(0, n - len(chosen) - 1)
    chosen += rng.sample(recent, min(want_recent, len(recent)))
    others = [g for g in rest if g not in chosen]
    while len(chosen) < n and others:
        g = rng.choice(others)
        others.remove(g)
        chosen.append(g)
    # de-duplicate on the shortened label: Twitch rejects identical choices
    out, labels = [], set()
    for g in chosen:
        lab = short(g["name"])
        if lab.lower() not in labels:
            labels.add(lab.lower())
            out.append({**g, "label": lab})
    return out[:n]
