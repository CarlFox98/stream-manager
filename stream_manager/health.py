"""Stream Health Monitor — catch problems before your viewers do.

Polls OBS (over obs-websocket) plus Stream Manager's own subsystems and turns
them into a set of named checks, each `ok` / `warn` / `bad`. When a check goes
bad it raises an **alert** that the dashboard shows as a banner + toast (and can
sound), optionally posts to chat, and always records to `data/health-log.jsonl`
so you can review a stream afterwards.

The metrics that matter most are the ones that diagnose dropped frames:

  * network drops  — GetStreamStatus.outputSkippedFrames / outputTotalFrames
                     (OBS's "dropped frames due to insufficient bandwidth")
  * congestion     — GetStreamStatus.outputCongestion (0..1)
  * rendering lag  — GetStats.renderSkippedFrames  / renderTotalFrames  (GPU)
  * encoding lag   — GetStats.outputSkippedFrames  / outputTotalFrames  (encoder)

Rates are measured **between polls**, not cumulatively, so a rough patch early
on doesn't mask a healthy stream later (and vice versa).
"""
import json, os, threading, time

from . import effects, obs_ws
from .config import BASE_DIR, config

LOG_FILE = os.path.join(BASE_DIR, "data", "health-log.jsonl")

DEFAULTS = {
    "enabled": True,
    "poll_sec": 5,
    "chat_alert": False,          # off by default: chat alerts are viewer-visible
    "chat_template": "⚠ Stream health: {message}",
    "chat_cooldown_sec": 300,
    "clear_after": 3,             # consecutive healthy samples before clearing
    # thresholds (percent unless noted)
    "dropped_warn": 1.0, "dropped_bad": 3.0,
    "congestion_warn": 0.3, "congestion_bad": 0.6,
    "render_warn": 1.0, "render_bad": 5.0,
    "encode_warn": 1.0, "encode_bad": 5.0,
    "cpu_warn": 85.0, "cpu_bad": 95.0,
    "ram_warn": 90.0, "ram_bad": 96.0,
    "disk_warn_mb": 10000, "disk_bad_mb": 2000,
    "fps_warn_pct": 90.0,         # % of target fps
    "overlay_stale_sec": 90,      # an overlay that polled before but went quiet
    "token_warn_sec": 900,        # warn when the Twitch token expires within this
}


def cfg(key):
    c = config.get("health")
    c = c if isinstance(c, dict) else {}
    return c.get(key, DEFAULTS.get(key))


_lock = threading.Lock()
_stop = threading.Event()
_prev = None                      # previous OBS sample, for per-interval rates
_alerts = {}                      # id -> {level, message, since, healthy_streak}
_last_chat = {}                   # id -> ts of last chat alert
_snapshot = {"checks": [], "metrics": {}, "alerts": [], "at": 0, "obs": False}


def _pct(part, total):
    try:
        return (float(part) / float(total)) * 100.0 if total else 0.0
    except Exception:
        return 0.0


def _chk(cid, label, status, message, value=None, unit=""):
    return {"id": cid, "label": label, "status": status, "message": message,
            "value": value, "unit": unit}


def _level(value, warn, bad, higher_is_worse=True):
    if higher_is_worse:
        return "bad" if value >= bad else ("warn" if value >= warn else "ok")
    return "bad" if value <= bad else ("warn" if value <= warn else "ok")


# ── the checks ─────────────────────────────────────────────────────────────
def _obs_checks(sample, checks, metrics):
    """Stream/render/encode health from OBS. Rates are per-interval."""
    global _prev
    stats, stream = sample.get("stats") or {}, sample.get("stream") or {}
    live = bool(stream.get("outputActive"))
    metrics["live"] = live
    metrics["congestion"] = round(float(stream.get("outputCongestion") or 0), 3)
    metrics["fps"] = round(float(stats.get("activeFps") or 0), 1)
    metrics["render_ms"] = round(float(stats.get("averageFrameRenderTime") or 0), 2)
    disk_mb = float(stats.get("availableDiskSpace") or 0)
    metrics["disk_mb"] = int(disk_mb)

    prev = _prev
    _prev = {
        "t": time.time(),
        "out_skipped": float(stream.get("outputSkippedFrames") or 0),
        "out_total": float(stream.get("outputTotalFrames") or 0),
        "render_skipped": float(stats.get("renderSkippedFrames") or 0),
        "render_total": float(stats.get("renderTotalFrames") or 0),
        "enc_skipped": float(stats.get("outputSkippedFrames") or 0),
        "enc_total": float(stats.get("outputTotalFrames") or 0),
        "bytes": float(stream.get("outputBytes") or 0),
    }

    def rate(key_s, key_t):
        if not prev:
            return None
        ds = _prev[key_s] - prev[key_s]
        dt = _prev[key_t] - prev[key_t]
        if dt <= 0:
            return 0.0 if ds <= 0 else 100.0
        return max(0.0, min(100.0, (ds / dt) * 100.0))

    # network drops — the one that ruined last night's stream
    if live:
        drop = rate("out_skipped", "out_total")
        if drop is not None:
            metrics["dropped_pct"] = round(drop, 2)
            lvl = _level(drop, float(cfg("dropped_warn")), float(cfg("dropped_bad")))
            checks.append(_chk("dropped", "Dropped frames (network)", lvl,
                               f"{drop:.1f}% of frames dropped — upload can't keep up"
                               if lvl != "ok" else f"{drop:.1f}%", round(drop, 2), "%"))
        cong = metrics["congestion"]
        lvl = _level(cong, float(cfg("congestion_warn")), float(cfg("congestion_bad")))
        checks.append(_chk("congestion", "Network congestion", lvl,
                           f"congestion {cong:.2f} — connection is struggling" if lvl != "ok"
                           else f"{cong:.2f}", cong))
        if stream.get("outputReconnecting"):
            checks.append(_chk("reconnecting", "Stream reconnecting", "bad",
                               "OBS is reconnecting to Twitch"))
        if prev:
            dt = max(0.001, _prev["t"] - prev["t"])
            kbps = ((_prev["bytes"] - prev["bytes"]) * 8 / 1000.0) / dt
            if kbps >= 0:
                metrics["bitrate_kbps"] = int(kbps)
        metrics["duration_sec"] = int((stream.get("outputDuration") or 0) / 1000)

    r = rate("render_skipped", "render_total")
    if r is not None:
        metrics["render_pct"] = round(r, 2)
        lvl = _level(r, float(cfg("render_warn")), float(cfg("render_bad")))
        checks.append(_chk("render", "Rendering lag (GPU)", lvl,
                           f"{r:.1f}% frames missed rendering" if lvl != "ok" else f"{r:.1f}%",
                           round(r, 2), "%"))
    e = rate("enc_skipped", "enc_total")
    if e is not None:
        metrics["encode_pct"] = round(e, 2)
        lvl = _level(e, float(cfg("encode_warn")), float(cfg("encode_bad")))
        checks.append(_chk("encode", "Encoding lag", lvl,
                           f"{e:.1f}% frames skipped by the encoder" if lvl != "ok" else f"{e:.1f}%",
                           round(e, 2), "%"))
    if disk_mb:
        lvl = _level(disk_mb, float(cfg("disk_warn_mb")), float(cfg("disk_bad_mb")),
                     higher_is_worse=False)
        checks.append(_chk("disk", "Free disk space", lvl,
                           f"{disk_mb/1024:.1f} GB free", int(disk_mb), "MB"))


def _system_checks(checks, metrics):
    from .state import state
    sysd = state.get("system", {})
    cpu = float(sysd.get("cpu") or 0)
    ram = float(sysd.get("ram_pct") or 0)
    metrics["cpu"], metrics["ram_pct"] = cpu, ram
    checks.append(_chk("cpu", "CPU", _level(cpu, float(cfg("cpu_warn")), float(cfg("cpu_bad"))),
                       f"{cpu:.0f}%", cpu, "%"))
    checks.append(_chk("ram", "Memory", _level(ram, float(cfg("ram_warn")), float(cfg("ram_bad"))),
                       f"{ram:.0f}%", ram, "%"))


def _integration_checks(checks, metrics):
    from . import chat, eventsub, redeems, twitch_auth, spotify
    from .state import state
    interactive = bool(config.get("interactive_enabled", True))

    a = twitch_auth.auth
    if a.get("status") == "ok":
        left = (a.get("expires_at") or 0) - time.time()
        lvl = "warn" if 0 < left < float(cfg("token_warn_sec")) else "ok"
        checks.append(_chk("auth", "Twitch auth", lvl,
                           "token expires soon — it will auto-refresh" if lvl == "warn" else "authorized"))
    else:
        checks.append(_chk("auth", "Twitch auth", "bad" if interactive else "warn",
                           a.get("error") or f"not authorized ({a.get('status')})"))

    if interactive:
        checks.append(_chk("chat", "Twitch chat", "ok" if chat.status.get("connected") else "bad",
                           "connected" if chat.status.get("connected")
                           else (chat.status.get("error") or "disconnected")))
        r = redeems.public_status()
        checks.append(_chk("redeems", "Channel-point redeems", "ok" if r.get("ready") else "warn",
                           "ready" if r.get("ready") else (r.get("error") or "setting up")))
        e = eventsub.public_status()
        if e.get("available"):
            checks.append(_chk("eventsub", "EventSub", "ok" if e.get("connected") else "warn",
                               f"{e.get('subs', 0)} subscriptions" if e.get("connected")
                               else (e.get("error") or "not connected")))
        else:
            checks.append(_chk("eventsub", "EventSub", "warn",
                               "websocket-client not installed — using polling"))

    if spotify.configured():
        s = spotify.public_status()
        checks.append(_chk("spotify", "Spotify", "ok" if s.get("status") == "ok" else "warn",
                           "connected" if s.get("status") == "ok" else "not connected"))

    checks.append(_chk("obs", "OBS", "ok" if state.get("obs", {}).get("running") else "warn",
                       "running" if state.get("obs", {}).get("running") else "not detected"))


def _overlay_checks(checks, metrics):
    """An overlay that used to poll and went quiet is probably a dead browser source."""
    stale_after = float(cfg("overlay_stale_sec"))
    live, stale = [], []
    for ch, age in sorted(effects.subscribers(max_age=10 ** 9).items()):
        (live if age <= stale_after else stale).append(f"{ch} ({int(age)}s)")
    metrics["overlays_live"] = len(live)
    if stale:
        checks.append(_chk("overlays", "Overlay sources", "warn",
                           "not polling: " + ", ".join(stale)))
    elif live:
        checks.append(_chk("overlays", "Overlay sources", "ok", ", ".join(live)))


# ── alert state machine ────────────────────────────────────────────────────
def _log(entry):
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        entry["ts"] = time.time()
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[health] could not write log: {e}")


def _update_alerts(checks):
    """Raise on `bad`, clear after `clear_after` healthy samples (hysteresis)."""
    from . import chat
    now = time.time()
    bad = {c["id"]: c for c in checks if c["status"] == "bad"}
    for cid, c in bad.items():
        if cid not in _alerts:
            _alerts[cid] = {"level": "bad", "label": c["label"], "message": c["message"],
                            "since": now, "healthy": 0}
            _log({"event": "alert", "id": cid, "label": c["label"], "message": c["message"]})
            print(f"[health] ALERT {c['label']}: {c['message']}")
            if cfg("chat_alert") and now - _last_chat.get(cid, 0) > float(cfg("chat_cooldown_sec")):
                _last_chat[cid] = now
                try:
                    chat.say(str(cfg("chat_template")).format(message=c["message"], label=c["label"]))
                except Exception:
                    pass
        else:
            _alerts[cid].update(message=c["message"], healthy=0)
    for cid in list(_alerts):
        if cid in bad:
            continue
        _alerts[cid]["healthy"] += 1
        if _alerts[cid]["healthy"] >= int(cfg("clear_after")):
            info = _alerts.pop(cid)
            _log({"event": "cleared", "id": cid, "label": info["label"],
                  "duration_sec": round(now - info["since"], 1)})
            print(f"[health] cleared: {info['label']}")


def sample():
    """Take one reading. Returns the snapshot dict."""
    checks, metrics = [], {}
    obs_sample = obs_ws.fetch_stats()
    metrics["obs_ws"] = bool(obs_sample.get("ok"))
    if obs_sample.get("ok"):
        _obs_checks(obs_sample, checks, metrics)
    _system_checks(checks, metrics)
    _integration_checks(checks, metrics)
    _overlay_checks(checks, metrics)
    with _lock:
        _update_alerts(checks)
        worst = "bad" if any(c["status"] == "bad" for c in checks) else (
            "warn" if any(c["status"] == "warn" for c in checks) else "ok")
        _snapshot.update({
            "checks": checks, "metrics": metrics, "at": time.time(),
            "obs": bool(obs_sample.get("ok")), "status": worst,
            "alerts": [{"id": k, **v} for k, v in _alerts.items()],
        })
        return dict(_snapshot)


def public_status():
    with _lock:
        return dict(_snapshot)


# ── preflight ──────────────────────────────────────────────────────────────
def preflight():
    """One-click 'am I ready to go live?' check. Read-only."""
    snap = sample()
    items = []
    for c in snap["checks"]:
        if c["id"] in ("dropped", "congestion", "reconnecting", "render", "encode"):
            continue                      # only meaningful once you're live
        items.append({"label": c["label"], "status": c["status"], "message": c["message"]})
    m = snap.get("metrics", {})
    if not m.get("obs_ws"):
        items.append({"label": "OBS WebSocket", "status": "warn",
                      "message": "not reachable — health metrics will be limited"})
    else:
        items.append({"label": "OBS WebSocket", "status": "ok", "message": "connected"})

    # Preflight is stricter than the live monitor: OBS not running is a blocker
    # here (you cannot go live without it), even though it is only a warning
    # while the monitor is idling.
    for i in items:
        if i["label"] == "OBS" and i["status"] == "warn":
            i["status"] = "bad"
            i["message"] = "not running — start OBS before going live"

    bad = [i for i in items if i["status"] == "bad"]
    warn = [i for i in items if i["status"] == "warn"]
    ready = not bad
    if bad:
        summary = f"Not ready — {len(bad)} blocker{'s' if len(bad) > 1 else ''} to fix"
    elif warn:
        summary = f"Ready to stream — {len(warn)} warning{'s' if len(warn) > 1 else ''} worth a look"
    else:
        summary = "Ready to stream — everything checks out"
    return {"ready": ready, "warnings": len(warn), "items": items, "summary": summary}


# ── loop ───────────────────────────────────────────────────────────────────
def _loop():
    while not _stop.is_set():
        try:
            if cfg("enabled"):
                sample()
        except Exception as e:
            print(f"[health] monitor error: {e}")
        _stop.wait(max(float(cfg("poll_sec")), 2.0))


def start():
    if getattr(start, "_thread", None) and start._thread.is_alive():
        return
    _stop.clear()
    start._thread = threading.Thread(target=_loop, daemon=True)
    start._thread.start()


def stop():
    _stop.set()
