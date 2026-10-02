"""OBS *write* access for wheel effects — deliberately small and allowlisted.

obs_ws.py promises to be read-only and stays that way. Everything that changes
OBS lives here instead, and it can only touch what config.json → obs_effects
names: one source (the PNGtuber) in one scene, and filters this module created
itself. It has no code path to switch scenes, start/stop outputs, or touch any
other source — a wheel spin must never be able to end the stream.

    "obs_effects": {
      "enabled": true,
      "source": "PNG TUBER",
      "scene": "StreamFox 98 - Base",
      "tint_filter": "PRISM Cursed Tint"
    }

Every change is made through timed.py, which snapshots the original state first
and restores exactly that snapshot afterwards.
"""
import math

from . import obs_ws
from .config import config

# Filters this module may create and toggle. Names are fixed so the allowlist
# can't be widened by editing config.json.
TINT_FILTER = "PRISM Cursed Tint"
_TINT_SETTINGS = {"hue_shift": 140.0, "saturation": 0.6, "contrast": 0.25, "gamma": -0.15}

# OBS alignment bit flags
_LEFT, _RIGHT, _TOP, _BOTTOM = 1, 2, 4, 8


def cfg():
    c = config.get("obs_effects") if isinstance(config.get("obs_effects"), dict) else {}
    return {
        "enabled": bool(c.get("enabled", True)),
        "source": c.get("source") or "PNG TUBER",
        "scene": c.get("scene") or "StreamFox 98 - Base",
    }


def _with_ws(fn):
    if obs_ws.websocket is None:
        raise RuntimeError("websocket-client is not installed")
    ws = obs_ws._connect()
    try:
        return fn(ws)
    finally:
        try:
            ws.close()
        except Exception:
            pass


def available():
    """Cheap readiness check for the wheel's eligibility gate."""
    c = cfg()
    if not c["enabled"] or obs_ws.websocket is None:
        return False
    try:
        return bool(_with_ws(lambda ws: _item_id(ws, c["scene"], c["source"])))
    except Exception:
        return False


def _item_id(ws, scene, source):
    d = obs_ws._request(ws, "GetSceneItemId", {"sceneName": scene, "sourceName": source})
    return d.get("sceneItemId")


def _allowed(target):
    c = cfg()
    if target != c["source"]:
        raise PermissionError(f"'{target}' is not the allowlisted OBS source")
    return c


# ── flip (rotate 180° about the item's visual centre) ──────────────────────
def _centre_offset(t):
    """Vector from the item's anchor (its alignment point) to its centre,
    in canvas pixels, accounting for the item's current rotation."""
    w, h = abs(float(t.get("width", 0))), abs(float(t.get("height", 0)))
    a = int(t.get("alignment", _LEFT | _TOP))
    dx = (w / 2 if a & _LEFT else -w / 2 if a & _RIGHT else 0.0)
    dy = (h / 2 if a & _TOP else -h / 2 if a & _BOTTOM else 0.0)
    r = math.radians(float(t.get("rotation", 0)))
    return dx * math.cos(r) - dy * math.sin(r), dx * math.sin(r) + dy * math.cos(r)


def flipped_transform(t):
    """The transform that shows the same item upside down in the same place."""
    ox, oy = _centre_offset(t)
    return {
        "positionX": float(t.get("positionX", 0)) + ox,
        "positionY": float(t.get("positionY", 0)) + oy,
        "alignment": 0,                                  # centre
        "rotation": (float(t.get("rotation", 0)) + 180.0) % 360.0,
    }


_TRANSFORM_KEYS = ("positionX", "positionY", "alignment", "rotation")


def capture_transform(target, params):
    c = _allowed(target)

    def go(ws):
        iid = _item_id(ws, c["scene"], target)
        t = obs_ws._request(ws, "GetSceneItemTransform",
                            {"sceneName": c["scene"], "sceneItemId": iid})["sceneItemTransform"]
        return {"item_id": iid, "transform": {k: t.get(k) for k in _TRANSFORM_KEYS + ("width", "height")}}
    return _with_ws(go)


def apply_flip(target, params, snap):
    c = _allowed(target)

    def go(ws):
        obs_ws._request(ws, "SetSceneItemTransform", {
            "sceneName": c["scene"], "sceneItemId": snap["item_id"],
            "sceneItemTransform": flipped_transform(snap["transform"])})
        return True, "flipped"
    return _with_ws(go)


def revert_transform(target, params, snap):
    if not snap:
        return True
    c = _allowed(target)

    def go(ws):
        iid = _item_id(ws, c["scene"], target)          # ids survive, but re-resolve anyway
        orig = {k: snap["transform"][k] for k in _TRANSFORM_KEYS if snap["transform"].get(k) is not None}
        obs_ws._request(ws, "SetSceneItemTransform", {
            "sceneName": c["scene"], "sceneItemId": iid, "sceneItemTransform": orig})
        return True
    return _with_ws(go)


# ── tint (a filter this module owns) ───────────────────────────────────────
def ensure_tint_filter(ws, source):
    """Create the tint filter on the allowlisted source if it is missing,
    disabled. Only ever creates TINT_FILTER — never edits other filters."""
    try:
        obs_ws._request(ws, "GetSourceFilter", {"sourceName": source, "filterName": TINT_FILTER})
        return False
    except RuntimeError:
        obs_ws._request(ws, "CreateSourceFilter", {
            "sourceName": source, "filterName": TINT_FILTER,
            "filterKind": "color_filter_v2", "filterSettings": _TINT_SETTINGS})
        obs_ws._request(ws, "SetSourceFilterEnabled",
                        {"sourceName": source, "filterName": TINT_FILTER, "filterEnabled": False})
        return True


def capture_tint(target, params):
    _allowed(target)

    def go(ws):
        ensure_tint_filter(ws, target)
        f = obs_ws._request(ws, "GetSourceFilter", {"sourceName": target, "filterName": TINT_FILTER})
        return {"enabled": bool(f.get("filterEnabled"))}
    return _with_ws(go)


def _set_tint(target, on):
    def go(ws):
        obs_ws._request(ws, "SetSourceFilterEnabled",
                        {"sourceName": target, "filterName": TINT_FILTER, "filterEnabled": bool(on)})
        return True
    return _with_ws(go)


def apply_tint(target, params, snap):
    _allowed(target)
    _set_tint(target, True)
    return True, "tinted"


def revert_tint(target, params, snap):
    _allowed(target)
    return _set_tint(target, bool((snap or {}).get("enabled", False)))
