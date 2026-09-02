# v0.9.0 — Stream Health Monitor

Last month a stream went out with ~30% dropped frames and nobody knew until
viewers said so in chat. This release makes that impossible to miss: Stream
Manager now watches the numbers that actually predict a bad stream and tells you
the moment they move — while you can still do something about it.

## Added

### Stream Health Monitor (`stream_manager/health.py`)
Polls OBS over obs-websocket plus Stream Manager's own subsystems every few
seconds and turns them into named checks, each `ok` / `warn` / `bad`.

**What it watches**

| Check | Source | Why it matters |
|---|---|---|
| Dropped frames (network) | `GetStreamStatus.outputSkippedFrames` | Upload can't keep up — the classic "you're lagging" |
| Network congestion | `GetStreamStatus.outputCongestion` | Early warning before drops start |
| Stream reconnecting | `GetStreamStatus.outputReconnecting` | You're off-air right now |
| Rendering lag (GPU) | `GetStats.renderSkippedFrames` | GPU can't compose the scene |
| Encoding lag | `GetStats.outputSkippedFrames` | Encoder is overloaded |
| Bitrate / FPS / frame time | `GetStats` + `GetStreamStatus` | Context for everything above |
| Free disk space | `GetStats.availableDiskSpace` | Recordings about to fail |
| CPU / memory | system poller | The usual suspects |
| Twitch auth · chat · redeems · EventSub | live subsystem state | Silent feature failure |
| Overlay heartbeats | effects long-poll | A browser source that quietly died |

Frame rates are measured **per poll interval**, not cumulatively, so a rough
patch at the start of a stream doesn't mask a healthy hour later — and, more
importantly, a healthy first hour doesn't hide trouble that starts now.

**Alerting** — a check going `bad` raises an alert with hysteresis (it clears
only after several consecutive healthy samples, so a single blip doesn't
flap). Every raise and clear is appended to `data/health-log.jsonl` for
post-stream review, and printed to the console.

### Dashboard: Health tab
- **Critical banner** across the top of the dashboard whenever anything is bad —
  visible from every tab.
- **Toast + a short beep** the moment an alert is raised (mutable, and rate-limited
  to one beep per 20s), plus a "Recovered" toast when it clears.
- **Live metric tiles** colour-coded by severity, and a full list of checks with
  plain-language messages.
- **Preflight** — one click answers "am I ready to go live?" It runs every check
  that makes sense while offline (auth, chat, EventSub, OBS, overlays, disk,
  CPU/RAM), skips the live-only ones, and treats **OBS not running** as a hard
  blocker rather than a warning.

### Optional chat alerts
Off by default, since they're viewer-visible. Turn on `health.chat_alert` to
have the bot post a notice (templated, with a cooldown) when the stream goes
bad — useful if you have mods watching chat but not your second monitor.

## Configuration

New `health` section in `config.json` — every threshold is editable, and the
Config tab validates it. Defaults:

```json
"health": {
  "enabled": true, "poll_sec": 5,
  "chat_alert": false, "chat_cooldown_sec": 300, "clear_after": 3,
  "dropped_warn": 1.0, "dropped_bad": 3.0,
  "congestion_warn": 0.3, "congestion_bad": 0.6,
  "render_warn": 1.0, "render_bad": 5.0,
  "encode_warn": 1.0, "encode_bad": 5.0,
  "cpu_warn": 85, "cpu_bad": 95, "ram_warn": 90, "ram_bad": 96,
  "disk_warn_mb": 10000, "disk_bad_mb": 2000,
  "overlay_stale_sec": 90, "token_warn_sec": 900
}
```

## API

- `GET /api/health/monitor` — current snapshot: checks, metrics, active alerts.
- `GET /api/health/preflight` — one-shot readiness report.

## Also in this release

- `obs_ws.fetch_stats()` — `GetStats` + `GetStreamStatus` over a single
  connection, so the monitor costs one round-trip per poll.
- Overlay heartbeat tracking in `effects` (`note_poll` / `subscribers` /
  `last_poll`), which is what makes "your browser source is dead" detectable.
- `validate_section("health", …)` so a bad threshold is rejected with a readable
  message instead of silently breaking the monitor.
- 7 new tests (43 total), covering threshold levels, config fallback, the
  raise/clear hysteresis path with synthetic OBS samples, both preflight
  behaviours, section validation, and heartbeat tracking.
