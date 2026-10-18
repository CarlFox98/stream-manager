# Stream Manager

A local web dashboard + overlay server for OBS streaming: OBS/Twitch/system status at a glance, browser-source overlays that survive theme switches, and a self-update mechanism — all served from a single Python script with no external services required.

## Features

- **Control-center dashboard** — a tabbed UI (Overview · Health · Interactive · Commands · Timers · Quotes · Config · Log) at `http://localhost:5000/dashboard`, with live OBS/Twitch/system status, a health strip, toasts, and a connection banner
- **Command Manager** — toggle any built-in command on/off (hand one to another bot) and create custom `!name → response` commands, right from the dashboard
- **Visual config editor** — edit cooldowns, redeems, wheels, automation, EventSub, and alerts live (saved to `config.json`, validated) — no hand-editing JSON
- **Real OBS status** via OBS's built-in WebSocket API, falling back to cross-platform process detection (psutil) if it's not configured
- **Twitch status** — live/offline, title, game, viewer count, via the Twitch Helix API
- **Overlay scene sets** — switch your whole overlay theme from the dashboard; OBS Browser Sources point at stable URLs
- **Interactive games & redeems** — coin flip, 50/50, slots, dice, 8-ball, duel, and weighted **Lucky** / **Risky** wheels via chat commands *and* channel-point redeems, with PRISM overlays (with sound). Plus a **quote system**, **timed messages**, **first-chatter & new-follower alerts**, raid/bits/sub hype via EventSub, opt-in automated outcomes, a leaderboard, and cooldowns. See **[INTERACTIVE.md](INTERACTIVE.md)**
- **Stream Health Monitor** — watches dropped frames, **sustained bitrate vs target** (catches the quality loss Dynamic Bitrate hides), congestion, render/encode lag, **a muted or mis-bound microphone**, CPU/RAM, free disk, chat/EventSub/auth and overlay heartbeats; raises a banner + toast + beep (and optionally a chat notice) the moment something goes wrong, logs every incident to `data/health-log.jsonl`, and offers a one-click **preflight** check before you go live
- **Chat feed for overlays** — every chat message is normalised and published on a `chat` effects channel, so an overlay can render chat with no second IRC connection and no second set of credentials. Badges, Twitch emotes, mentions, replies, cheers, `/me` and moderation (`CLEARMSG` / `CLEARCHAT`) all come through. `GET /api/chat/backfill?n=25` returns the newest messages still in the ring buffer so an overlay repopulates after an OBS refresh. PRISM's chat overlay is deployed into `static/chat/` and consumes it
- **Real-time** — overlays get effects instantly (long-poll) and the dashboard streams live events (SSE)
- **Spotify now-playing** — one-click connect, a `!song` command, and a dashboard widget (optional)
- **One-click Twitch login** — a browser window opens for you to approve; no codes to copy
- **Start, restart and shut down without a console** — Restart and Shut Down buttons on the dashboard (two clicks, no undo), a system-tray icon that owns the process, and a `prism-ctl` command a Stream Deck key can run. Restarting re-reads `config.json` and reconnects Twitch and OBS without touching the port your browser sources point at
- **Claude integration (optional)** — a read-only MCP server lets Claude check OBS, Twitch, preflight and stream health for you. See **[Claude (MCP server)](#claude-mcp-server)**
- **Self-update** — checks GitHub Releases and installs from the dashboard (HTTPS-only, integrity-logged, with a backup first)
- **Local by default & LAN-safe** — binds to `127.0.0.1`; with `--lan`, controls stay token-locked and you can require a password for remote devices

## Requirements

- Python 3.9+
- OBS Studio (this is built to run alongside OBS, primarily on Windows — OBS/system detection uses Windows-specific APIs where OBS WebSocket isn't configured)
- Optional: `pip install -r requirements.txt` for CPU/RAM monitoring (`psutil`) and real OBS status (`websocket-client`)

## Setup

1. **Get the code**
   ```
   git clone https://github.com/CarlFox98/stream-manager.git
   cd stream-manager
   ```
   (Or download and extract the ZIP from GitHub if you don't have git.)

2. **Install dependencies**
   ```
   pip install -r requirements.txt
   ```

3. **Create your `.env` file** — copy `.env.example` to `.env` and fill in:
   - `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET` — from https://dev.twitch.tv/console/apps (register an application). Set the **OAuth Redirect URL** to `http://localhost:5000/auth/callback` so the one-click login can redirect back. (No `.env` yet? Just run the app once — it creates one from `.env.example` for you to fill in.)
   - `OBS_WEBSOCKET_PASSWORD` (optional) — in OBS: **Tools → WebSocket Server Settings → Enable WebSocket Server → Show Connect Info**, copy the password shown

4. **Check `config.json`** (see [Configuration](#configuration) below) — in particular `twitch_user` and `assets_dir`.

5. **(Optional) Set up overlay scene sets** — inside your `assets_dir`, create `overlays/modern/` and/or `overlays/retro/`, each containing the same overlay filenames (e.g. `starting-soon.html`, `be-right-back.html`, `stream-ending.html`, `tech-difficulties.html`) styled differently. Skip this if you just want a single fixed set of overlays — the app works fine without it.

6. **Run it**
   ```
   python stream-manager.py
   ```
   Your browser opens the dashboard automatically. If interactive features are on and you haven't logged in yet, a second tab opens on Twitch's consent screen — click **Authorize** once and you're connected (no codes to copy). Leave the terminal window running while you stream.

7. **(First run only, if using scene sets)** On the dashboard, click a scene set (e.g. **Modern Neon**) once — this copies its files into `overlays/active/`, which is what OBS actually reads.

8. **Point OBS at your overlays** — add a Browser Source for each one, using the URLs shown (and click-to-copy) under **Overlay URLs** on the dashboard, e.g. `http://localhost:5000/overlays/active/starting-soon.html`. These stay the same even after switching scene sets.

## Build a standalone `.exe` (optional)

To run without a Python install, build a single executable with PyInstaller (on Windows):

```
build-exe.bat
```

This produces `dist\StreamManager.exe`. Put your `config.json` and `.env` next to the exe before running it; `data/` and logs are created alongside it, and `static/` is bundled inside. (The in-app self-updater is for source installs; rebuild the exe to update it.)

## Configuration

### `config.json`

| Key | Default | Meaning |
|---|---|---|
| `port` | `5000` | Port to listen on (tries the next 19 ports if taken) |
| `poll_interval` | `5` | Seconds between OBS/Twitch/system status polls |
| `twitch_user` | — | Your Twitch login name |
| `assets_dir` | `%USERPROFILE%\Pictures\OBS Assets` | Where overlay files (and `overlays/modern`, `overlays/retro`, `overlays/active`) live |
| `log_file` | `server.log` | Request log file, auto-rotated at 1 MB |
| `lan` | `false` | Bind to `0.0.0.0` instead of `127.0.0.1` — see [Security](#security) |

### `.env`

| Variable | Required? | Purpose |
|---|---|---|
| `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET` | For Twitch status | Twitch Helix API credentials |
| `OBS_WEBSOCKET_PASSWORD` | For real OBS status | OBS WebSocket server password |
| `OBS_WEBSOCKET_HOST` / `OBS_WEBSOCKET_PORT` | No | Only needed if OBS's WebSocket server isn't on `localhost:4455` |

## CLI flags

```
python stream-manager.py [flags]

--port PORT        Port to listen on (overrides config.json)
--poll SECONDS      Poll interval (overrides config.json)
--no-browser        Don't open the dashboard in a browser on start
--lan               Bind to 0.0.0.0 so other devices on your network can reach the dashboard
--check-update      Check GitHub for a newer version and exit
--update            Check, then (after confirmation) download & install the latest version
--version           Print the version and exit
```

Exit codes matter here: **42** means "restart me". `Start Stream Manager.bat` and the
tray app both relaunch on 42 and stop on anything else, which is how the Restart
button works at all. A bare `python stream-manager.py` has nothing listening for
it, so the dashboard reports that instead of quietly shutting down.

## Starting and stopping

| You want to | Do this |
|---|---|
| Start it | `Start Stream Manager.bat` — it relaunches itself on request |
| Start it with a tray icon | `Stream Manager (Tray).bat` (needs `pip install pystray pillow`) |
| Restart mid-stream | Dashboard → Overview → **Restart**, the tray menu, or `prism-ctl restart` |
| Shut it down | Dashboard → **Shut Down**, the tray menu, or `prism-ctl stop` |
| Put it on a Stream Deck | A **System → Open** key pointing at `prism-ctl.bat` with the argument `restart` |

```
prism-ctl.bat status | start | restart | stop
```

`prism-ctl` reads `data/runtime.json` — the port and this run's session token,
written when the app starts and removed when it stops. That file never leaves
your disk and `data/` is gitignored; a Stream Deck key needs no credential of
its own and nothing is added to `.env`.

Restart and Shut Down are **local-only**. They require this run's session token
*and* a request from `127.0.0.1`, so even in `--lan` mode with the dashboard
password set, another device on your network can view the dashboard but cannot
stop the thing running your stream.

## Claude (MCP server)

Stream Manager ships an optional [MCP](https://modelcontextprotocol.io) server so
Claude can check on your stream: is OBS live, is preflight green, what is the
health monitor saying, is a raid waiting for a shoutout. It is **read-only** in
this release.

| Tool | What it answers |
|---|---|
| `sm_status` | Running or not; OBS streaming/recording/scene; Twitch live, title, game, viewers; PC load |
| `sm_preflight` | The dashboard's "am I ready to go live?" check |
| `sm_health` | Live health monitor: alerts and every check |
| `sm_shoutout_status` | Shoutouts on/off, pending raid, last shoutout, clip playing |
| `sm_scene_sets` | Active overlay theme and the installed ones |
| `sm_stream_stats` | Game and wheel stats |
| `sm_active_effects` | Running wheel effects and rewards still owed |
| `sm_update_check` | Installed vs latest version (asks GitHub) |

**Setup** (Python 3.10+):

1. `pip install -r requirements-mcp.txt`
2. Add it to Claude's desktop config (`%APPDATA%\Claude\claude_desktop_config.json`
   on Windows) under `mcpServers`, with your own path:
   ```json
   "stream-manager": {
     "command": "C:\\path\\to\\stream-manager\\mcp-server.bat"
   }
   ```
   No `mcp-server.bat` yet (the 0.14.0 self-update can't add new files)? This
   form does the same thing:
   ```json
   "stream-manager": {
     "command": "python",
     "args": ["-m", "stream_manager.mcp_server"],
     "env": { "PYTHONPATH": "C:\\path\\to\\stream-manager" }
   }
   ```
3. Restart Claude. Ask it "is my stream healthy?".

How it stays safe:

- It is a separate process that Claude launches and talks to over stdin/stdout.
  It opens **no port**, and never starts a second copy of the app.
- Like `prism-ctl`, it finds the running app through `data/runtime.json` and
  talks to it on `127.0.0.1` only (proxy settings are ignored). Every tool is a
  plain GET; the session token is never sent, returned or logged.
- It never imports the app's config, so `.env` secrets are not even loaded into it.
- Replies are built from allowlists, so a field added to the app later stays out
  of Claude's context until someone decides it belongs there.
- Viewer names, wheel labels and owed-reward text are written by viewers; the
  server tells Claude to treat them as data, never instructions. Chat itself is
  not exposed.

## Security

By default the server only listens on `127.0.0.1` — nothing else on your network can reach the dashboard or its API. Pass `--lan` (or set `"lan": true` in `config.json`) if you want to check the dashboard from your phone or another device on the same network; the startup banner always states plainly which mode is active.

Two things guard it. Every state-changing endpoint requires a session token that is generated fresh each run and compared in constant time, so a web page you happen to visit cannot POST to the dashboard. And in `--lan` mode, setting `SM_DASHBOARD_PASSWORD` requires HTTP Basic auth from any non-localhost client; localhost (OBS on the same PC) is always exempt.

Restart and shutdown get a third gate: the request must come from `127.0.0.1`, whatever the token says. Note that in `--lan` mode *without* `SM_DASHBOARD_PASSWORD`, anyone who can load `/dashboard` can read the session token out of the page — so for every other protected endpoint the token is not doing much there, and the password is what matters. The loopback gate is why restart and shutdown are unaffected either way.

That is enough to keep a stray browser tab or a curious housemate out. It is not hardening for a hostile network: there is no TLS, and without `SM_DASHBOARD_PASSWORD` the `--lan` dashboard is readable by anyone who can reach the port. Only enable `--lan` on networks you trust.

## Updating

The updater reads GitHub **Releases** (not tags), so a version only reaches anyone once a Release is published for its tag.

The dashboard shows a banner when a newer version is available on GitHub, with a one-click install (your current files are backed up to `.update-backup/` first, then hit **Restart** on the Overview tab to apply). Or from the command line: `python stream-manager.py --check-update` / `--update`.
