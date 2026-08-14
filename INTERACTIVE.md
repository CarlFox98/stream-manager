# Interactive Features — Games & Redeems

This adds viewer-facing interaction to Stream Manager: **coin flip**, **50/50**,
a **Lucky Wheel** and a **Risky Wheel**, plus a **quote system** — all triggerable
by **chat commands** and/or **Twitch channel-point redeems**, with **PRISM-styled
OBS overlay animations** and **chat replies**.

It's built the same way as the rest of the app: standard-library only (raw TLS
IRC instead of a websocket dependency; channel-point **polling** via the Helix
API instead of EventSub), local by default, no external services.

---

## What you get

| Feature | Chat command | Channel-point redeem | On-stream |
|---|---|---|---|
| Coin flip | `!coinflip` / `!flip` | *Coin Flip* (off by default) | coin overlay + chat |
| 50 / 50 | `!5050` | *50/50* (off by default) | coin overlay + chat |
| Slots | `!slots` | (add via config) | slots overlay + chat |
| Dice | `!dice [sides]` / `!roll` | — | chat |
| Magic 8-ball | `!8ball <question>` | — | chat |
| Duel | `!duel @user` | — | chat |
| Lucky Wheel | `!lucky` (mods) | **Lucky Wheel Spin** | wheel overlay + chat |
| Risky Wheel | `!risky` (mods) | **Risky Wheel Spin** | wheel overlay + chat |
| Quotes | `!quote [n]`, `!addquote`, `!delquote`, `!quotecount` | — | chat |
| Raid / Bits / Sub hype | — | (automatic via EventSub) | hype overlay + chat |

The two wheels are the redeem-driven headliners; coin flip / 50-50 / slots default
to chat commands (flip their `enabled` flag in `config.json` → `redeems` to also
expose them as channel-point redeems). Raid/bits/sub hype fires automatically when
EventSub is connected.

## New in v0.6.0

- **One-click Twitch login** — a browser window opens straight to Twitch's
  consent screen; click **Authorize** once and you're done. No more copying a
  code or a link. Re-authorize or sign out any time from the dashboard.
- **Locked-down controls** — every state-changing endpoint (scene switch,
  update install, config reload, authorize, quotes) now requires the
  dashboard's per-run token, so nothing else on your network (or a random
  website you visit) can drive them. The server is back to **localhost-only**
  by default.
- **Hardened serving** — overlay/static file serving is stricter about staying
  inside its folder, dashboard output is HTML-escaped (no injection via
  usernames/overlay labels), and the JSON API no longer sends wildcard CORS.

## New in v0.5.0

- **EventSub** — near-instant redemptions and **raid / bits / sub** hype (needs
  the optional `websocket-client`; falls back to polling automatically).
- **Automated wheel outcomes** — segments can auto **switch scene / grant VIP /
  shoutout / timeout** (opt-in, allowlisted; see *Automation* below).
- **More games** — slots, dice, 8-ball, duel.
- **Sound** on the overlays (synthesized, no files; mute with `?muted`).
- **Leaderboard & stats**, **config hot-reload** (♻️ on the dashboard), reward
  **de-dupe by ID**, **refund-on-failure**, and **persistent** cooldowns/anti-spam.

---

## One-time setup

1. **Register the redirect URI.** In <https://dev.twitch.tv/console/apps>, open
   your existing app (the one whose `TWITCH_CLIENT_ID` is in `.env`) and add this
   **OAuth Redirect URL**:

   ```
   http://localhost:5000/auth/callback
   ```

   (If Stream Manager binds a different port because 5000 was busy, the console
   banner and dashboard show the exact URL to register — it's also printed as
   **Redirect** in the startup banner.) Either **Public** or **Confidential**
   client type works: with a client secret in `.env` the app uses the standard
   secret exchange; without one it uses PKCE automatically.

2. **Run it and click Authorize.** `python stream-manager.py`. On first launch a
   **browser window opens straight to Twitch's consent screen** — approve with
   **your broadcaster account** and you're connected. Nothing to copy or paste.
   If the window doesn't open (e.g. a headless box), the console prints the link
   and the dashboard's *Interactive* card has a **Login with Twitch** button.

   The token is cached in `.twitch_user_token.json` (gitignored) and
   auto-refreshes; you won't be asked again unless you change the requested
   scopes, sign out, or revoke access. Use **Sign out** on the dashboard to
   re-authorize (e.g. to switch to a bot account).

   Scopes requested: `chat:read`, `chat:edit`, `channel:read:redemptions`,
   `channel:manage:redemptions`, `bits:read`, `channel:read:subscriptions`,
   `channel:manage:vips`, `moderator:manage:banned_users`,
   `moderator:manage:shoutouts`. (The last three power the automated outcomes;
   if you'd rather not grant them, set `automation.enabled` to `false` in
   `config.json` — everything else still works.)

3. **Rewards auto-create.** On startup the app ensures the enabled channel-point
   rewards exist (creating *Lucky Wheel Spin* and *Risky Wheel Spin* if missing).
   Because they're created by this app, it can also read and fulfil their
   redemptions. Adjust cost/prompt/enabled in `config.json` → `redeems`.

4. **Add the OBS overlays.** Add **Browser Sources**, all transparent,
   **1920×1080**, placed **above** your scene:

   - `http://localhost:5000/static/interactive/wheel.html`  (Lucky + Risky)
   - `http://localhost:5000/static/interactive/coinflip.html`  (coin + 50/50)
   - `http://localhost:5000/static/interactive/slots.html`  (slot machine)
   - `http://localhost:5000/static/interactive/hype.html`  (raid / bits / sub)

   They show nothing until something is triggered, then animate and fade out.
   Add `?muted` to a URL to silence its sound, or `?vol=0.4` to set volume.
   (Copy buttons are on the dashboard's *Interactive* card.)

That's it. Redeem the wheel or type `!coinflip` and you'll see the overlay play
and a chat message post.

---

## Testing without going live

The dashboard's **Interactive** card has 🪙 / 🎲 / 🍀 / ☠️ buttons that fire each
effect straight to the overlays (and to chat if connected). You can also preview
an overlay's animation standalone in a browser:

- `…/wheel.html?demo=lucky` or `…/wheel.html?demo=risky`
- `…/coinflip.html?demo`

---

## Customizing the wheels

Edit `config.json` → `wheels`. Each segment has a `label` and an optional
`weight` (relative odds; default 1) and `color` (defaults cycle the PRISM
palette). Example:

```json
"wheels": {
  "lucky": {
    "title": "Lucky Wheel",
    "color": "#57F2E4",
    "segments": [
      { "label": "Show your sona on the starting screen 🦊", "weight": 3 },
      { "label": "VIP for a week", "weight": 1 },
      { "label": "Mod for 5 minutes", "weight": 1 }
    ]
  }
}
```

Higher weight = lands more often. Keep 6–10 segments for readability. Emoji work.

## Cooldowns & abuse limits

Two independent layers stop spam:

**Redeems** are limited by Twitch itself, *before* a viewer's points are spent —
set per redeem in `config.json` → `redeems`:

| Field | Default (wheels) | Meaning |
|---|---|---|
| `global_cooldown` | `60` | Seconds nobody can redeem it again (`0` = off) |
| `max_per_user_per_stream` | `3` | Per-viewer cap each stream (`0` = unlimited) |
| `max_per_stream` | `0` | Total cap each stream (`0` = unlimited) |

These are pushed onto the Twitch reward on startup and **kept in sync** — edit
them in `config.json`, restart, and existing rewards are updated (no need to
delete/recreate).

**Chat commands** (`!coinflip`, `!5050`, `!lucky`, `!quote`, …) use an app-side
cooldown in `config.json` → `cooldowns`, with a per-viewer (`user`) and
everyone (`global`) window in seconds:

```json
"cooldowns": {
  "mods_bypass": true,
  "coinflip": { "user": 30, "global": 3 },
  "lucky":    { "user": 60, "global": 5 }
}
```

`mods_bypass: true` lets you and your mods trigger without waiting. A blocked
viewer gets a single brief "on cooldown" reply (throttled so it can't spam).

> Note: coinflip / 50-50 don't award channel points in the code — a "WIN" is
> just a fun outcome — so they can't be farmed for points. The cooldown is there
> purely to stop overlay/chat spam.

### Wheel anti-spam (no-repeat + severe cooldown)

On top of the Twitch caps, each wheel has two per-viewer guards (set on the
wheel in `config.json` → `wheels.<lucky|risky>`):

- `no_repeat` (default `true`) — a viewer never lands on the **exact same slot
  twice in a row** (as long as another slot is available).
- `severe_cooldown_seconds` (default `900` on the risky wheel) — mark any harsh
  slot with `"severe": true` (the 60s timeout is, by default) and a viewer who
  hits a severe slot can't hit *any* severe slot again for that many seconds.
  Everyone else's spins are unaffected.

This is tracked per viewer in memory (resets when you restart), so one person
can't get chain-timed-out by bad luck or by farming redeems.

## Security — keep privileges off the wheel

The default Lucky Wheel deliberately contains **no "temp mod" / privilege
rewards.** Granting moderator powers for a few hundred channel points is a real
risk: mods can ban, delete messages, edit your stream info, and run commands.
VIP is much safer (cosmetic; no moderation powers) and is included, but if you
ever add a privilege reward, gate it behind a very high cost and a tiny weight.

Also by design the app only **announces** outcomes — it never auto-mods, auto-VIPs,
or times anyone out. You grant rewards yourself, so a wheel result can't directly
change anyone's permissions.

## Automation — auto-granting outcomes (opt-in)

By default outcomes are announced and logged for you to grant. If you turn on
`automation.enabled` in `config.json`, a wheel segment with an `"action"` field
will actually perform it. The allowlist (anything else is ignored):

| action | what it does | scope |
|---|---|---|
| `scene:<set>` | switch overlay scene set (e.g. `scene:retro`) | — |
| `vip` | grant the redeeming viewer VIP | `channel:manage:vips` |
| `shoutout` | `/shoutout` the viewer (or `shoutout:<login>`) | `moderator:manage:shoutouts` |
| `timeout:<sec>` | time the redeeming viewer out (self-inflicted) | `moderator:manage:banned_users` |

Example segment: `{ "label": "VIP for a week", "weight": 1, "action": "vip" }`.
Per-action switches (`allow_vip`, `allow_timeout`, …) let you disable any one
without editing the wheel. There is **no `mod` action** — that's intentional.
Automation only fires when there's a real target viewer, so dashboard test spins
won't VIP/timeout anyone (a `scene:` action will still switch, for testing).

## EventSub (near-instant + raids/bits/subs)

With `websocket-client` installed, the app opens Twitch's EventSub websocket for
instant redemptions and **raid / bits / sub** events that drive the hype overlay
(and, optionally, a free Lucky spin on raid via `eventsub.raid_free_spin`). The
dashboard shows an **EventSub ⚡** chip when it's live; otherwise it says
**Polling** and everything still works with a few seconds' latency. Toggle event
types under `config.json` → `eventsub`.

## Leaderboard, stats & hot-reload

The dashboard's *Interactive* card shows a live leaderboard (most active players,
wins) and recent results, backed by `data/stats.json`. Edited the wheels or
cooldowns? Hit **♻️ Reload config** (or `POST /api/interactive/reload`) to apply
`config.json` changes — including pushing new reward costs/limits to Twitch —
without restarting.

### Granting non-automated rewards
Outcomes without an `action` (e.g. *show your sona*, *pick the next song*) are
announced on the overlay and in chat and logged in the dashboard's *Recent
results* feed — you grant those yourself.

---

## Configuration reference (`config.json`)

| Key | Default | Meaning |
|---|---|---|
| `interactive_enabled` | `true` | Master switch for the whole interactive layer |
| `command_prefix` | `"!"` | Chat command prefix |
| `redeem_poll_interval` | `3` | Seconds between channel-point redemption polls |
| `wheels.lucky` / `wheels.risky` | see file | Title, color, weighted segments |
| `redeems.auto_fulfill` | `true` | Mark redemptions FULFILLED after handling (points spent). Set `false` to leave them in your queue |
| `redeems.<action>` | see file | Per-redeem `title`, `cost`, `enabled`, `prompt`, plus limits `global_cooldown`, `max_per_user_per_stream`, `max_per_stream` |
| `cooldowns` | see file | Chat-command cooldowns: `mods_bypass`, and per-command `{ "user": s, "global": s }` |

---

## How it works (architecture)

```
Twitch chat ──IRC/TLS──► chat.py ─┐
Channel points ─Helix poll─► redeems.py ─┤──► games.py ──► effects.py ──poll──► overlays (wheel/coinflip .html)
Dashboard test button ──────────┘            │
                                             └──► chat.say (chat reply)
                        twitch_auth.py  (user token via Authorization Code + local callback, cached + refreshed)
```

- **twitch_auth.py** — user token via the Authorization Code flow with a local
  `/auth/callback` redirect (one-click browser login, PKCE or secret); cache +
  refresh; resolves `broadcaster_id`.
- **chat.py** — raw TLS IRC client (`irc.chat.twitch.tv:6697`), tags parsing, reconnect, `say()`.
- **redeems.py** — ensures rewards exist, polls the UNFULFILLED queue, dispatches, fulfils.
- **games.py** — coinflip / 50-50 / weighted wheels + the chat-command dispatcher.
- **quotes.py** — numbered, JSON-persisted quote book (`data/quotes.json`).
- **effects.py** — in-memory event bus; overlays long-poll `/api/effects/<channel>?since=<id>`.
- **overlays** — `static/interactive/{wheel,coinflip}.html`, self-contained PRISM-styled.

New HTTP endpoints: `GET /api/effects/<channel>`, `GET /api/interactive`,
`GET /api/quotes`, `GET /auth/callback` (Twitch redirect), `POST /api/interactive/test`,
`POST /api/interactive/authorize`, `POST /auth/logout`, `POST /api/quotes/add`,
`POST /api/quotes/delete`. State-changing `POST`s require the `X-SM-Token`
header the dashboard sends automatically.

### Why polling instead of EventSub
Channel-point EventSub needs a websocket client; the Helix
`Get Custom Reward Redemptions` endpoint is pollable with the standard library
and matches the app's existing status-poll design. Latency is just
`redeem_poll_interval` seconds. If you ever want sub-second redemptions, dropping
in an EventSub websocket is a clean future upgrade.

---

## Notes & limits

- Requires **Twitch Affiliate/Partner** for channel points (you're set). Chat
  commands work regardless.
- Rewards are only manageable/fulfillable because *this app* creates them — a
  reward you made by hand in the Twitch dashboard can't have its redemptions
  read via the API, so let the app create the wheel rewards.
- The bot posts as **your** account (the one you authorize). To use a separate
  bot account, authorize with that account instead — just make sure it's a mod
  in your channel.
- Everything binds to `127.0.0.1` unless you pass `--lan`.
