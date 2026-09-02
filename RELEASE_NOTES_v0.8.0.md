# v0.8.0 — Shoutouts built in (PRISM Shoutout merged)

The standalone **PRISM Shoutout** service is now part of Stream Manager. That
retires a second Python process, a second Twitch chat connection, a second
obs-websocket client, a second overlay transport, a duplicated copy of the
Twitch credentials, and an 18 MB virtualenv — one app, one console, one config.

## Added

### Shoutouts (`stream_manager/shoutout.py`)
- **`!so @user`** (alias `!shoutout`), mods-only by default, accepting `@name`,
  a bare login, or a full `twitch.tv/...` URL.
- **Automatic raid shoutouts** via EventSub, with a viewer floor, an optional
  allowlist, and an optional "hold until a mod says `!so ok`" approval flow.
- **Mod controls:** `!so skip · clear · off · on · ok · status`.
- **Clip selection, ported intact** — newest clip from the last 7 days (random
  among the newest 8), falling back to a random pick from the most-viewed of the
  last 30 days, skipping the last few clips shown for that streamer so repeat
  shoutouts rotate. Rotation survives restarts via `data/shoutout-log.jsonl`.
- **Signed-MP4 resolution** so clips autoplay without a mature-content gate,
  with the legacy thumbnail fallback for older clips.
- **Safety gates carried over:** blocklist, no self-shoutout, per-login repeat
  guard that outlives a queued card, a screen-time queue model, a lookup guard
  covering the multi-call Helix lookup, and a queue-depth cap.
- **Chat templates** for normal / raid / live / not-found.

### Overlay
- `static/interactive/shoutout.html` — the PRISM card, re-pointed from its old
  WebSocket service to Stream Manager's effects transport (long-poll, same as
  the other overlays). Reports clip start/stop back to
  `POST /api/shoutout/clip` (the hook for OBS audio ducking).

### Dashboard & API
- `GET /api/shoutout` status, a **◇ Shoutout** quick-test button, the overlay in
  the copy-able URL list and the live preview switcher, and a `shoutout` section
  in the visual config editor (validated).

## Changed
- `__version__` → `0.8.0`; new `config.json` section: `shoutout` (every value
  optional, defaults carried over from PRISM's tuned settings).
- `twitch.py` gained `invalidate_token()` so Helix calls can retry once on 401.

## Notes
- Uses the Twitch token Stream Manager already holds — no extra credentials.
- The PRISM **web assets** (scenes, widgets, fonts, branding) are unchanged and
  still served from your OBS assets folder; only the service is retired.
- Not yet ported: OBS **audio ducking** while a clip plays. The overlay already
  reports clip start/stop, so it's a drop-in next step.
