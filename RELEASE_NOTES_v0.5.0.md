# v0.5.0 — Interactive community layer

A big feature release: viewer-facing games and channel-point redeems with
PRISM-styled OBS overlays, chat integration, and (opt-in) automated outcomes.
All standard-library by default; `websocket-client` is an optional upgrade.

## Added

### Games & redeems
- **Coin flip**, **50/50**, **slot machine**, **dice**, **magic 8-ball**, and a
  viewer **duel** — via chat commands and/or Twitch channel-point redeems.
- **Lucky Wheel** and **Risky Wheel** — weighted, PRISM-styled canvas overlays.
- **Quote system** — `!quote [n]`, `!addquote`, `!delquote`, `!quotecount`.

### Overlays (transparent OBS browser sources, 1920×1080)
- `static/interactive/{wheel,coinflip,slots,hype}.html` + shared `fx.js`.
- Synthesized **sound** cues (no audio files); `?muted` / `?vol=` supported.

### Twitch integration
- **User-token auth** via Device Code Flow (cached + auto-refreshed).
- **Chat** over raw TLS IRC (no dependency), mod/broadcaster-aware rate limiting.
- **Channel points** with Twitch-native limits (global cooldown,
  max-per-user-per-stream), reward **de-dupe by ID**, **refund-on-failure**, and
  **backlog handling** (ignore pre-startup redemptions unless `catch_up`).
- **EventSub** (optional `websocket-client`) for near-instant redemptions and
  **raid / bits / sub** hype, with automatic **polling fallback**.

### Automation (opt-in, allowlisted)
- Wheel segments can auto **switch scene / grant VIP / shoutout / timeout**.
  No privilege-escalation (`mod`) action, by design.

### Safety & anti-abuse
- Per-command cooldowns (per-user + global, mods bypass), wheel **no-repeat** and
  **severe-slot cooldown**, all **persisted** across restarts.
- No "temp mod" on the default wheel.

### Dashboard & ops
- *Interactive* card: device-code login, test buttons, EventSub/automation chips,
  **leaderboard**, recent results, copy-able overlay URLs, and **config hot-reload**.
- New endpoints: `/api/effects/<channel>`, `/api/interactive`,
  `/api/interactive/stats`, `/api/interactive/reload`, `/api/quotes`, and
  `/api/interactive/{test,authorize}`.
- Server upgraded to **ThreadingHTTPServer**.

## Changed
- `__version__` → `0.5.0`.
- `config.json` gains `interactive_enabled`, `command_prefix`, `redeem_poll_interval`,
  `wheels`, `redeems`, `cooldowns`, `automation`, `eventsub`.
- `.env.example` documents the expanded scopes; Twitch app must be **Public**.

## Notes
- Requires re-authorizing once (new scopes).
- Automated VIP/timeout/shoutout need the corresponding scopes; disable via
  `automation.enabled: false` to skip them.
- Runtime data lives in `data/` (gitignored): quotes, stats, reward IDs,
  cooldowns, spin history.
