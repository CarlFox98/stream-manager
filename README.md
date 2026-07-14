# Stream Manager

A local web dashboard + overlay server for OBS streaming: OBS/Twitch/system status at a glance, browser-source overlays that survive theme switches, and a self-update mechanism — all served from a single Python script with no external services required.

## Features

- **Dashboard** — live OBS, Twitch, and system (CPU/RAM/GPU) status, refreshed every few seconds, at `http://localhost:5000/dashboard`
- **Real OBS status** via OBS's built-in WebSocket API (current scene, actual streaming/recording state), falling back automatically to basic process detection if it's not configured
- **Twitch status** — live/offline, title, game, viewer count, via the Twitch Helix API
- **Overlay scene sets** — switch your whole overlay theme (e.g. "Modern" vs "Retro") from the dashboard; OBS's Browser Sources point at stable URLs that never change, even when you switch themes
- **Self-update** — checks GitHub Releases and can download + install a new version from the dashboard, with an automatic backup first
- **Local by default** — binds to `127.0.0.1` only; nothing on your network can reach it unless you explicitly opt in

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
   - `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET` — from https://dev.twitch.tv/console/apps (register an application, OAuth redirect URL `http://localhost`)
   - `OBS_WEBSOCKET_PASSWORD` (optional) — in OBS: **Tools → WebSocket Server Settings → Enable WebSocket Server → Show Connect Info**, copy the password shown

4. **Check `config.json`** (see [Configuration](#configuration) below) — in particular `twitch_user` and `assets_dir`.

5. **(Optional) Set up overlay scene sets** — inside your `assets_dir`, create `overlays/modern/` and/or `overlays/retro/`, each containing the same overlay filenames (e.g. `starting-soon.html`, `be-right-back.html`, `stream-ending.html`, `tech-difficulties.html`) styled differently. Skip this if you just want a single fixed set of overlays — the app works fine without it.

6. **Run it**
   ```
   python stream-manager.py
   ```
   Your browser opens the dashboard automatically. Leave the terminal window running while you stream.

7. **(First run only, if using scene sets)** On the dashboard, click a scene set (e.g. **Modern Neon**) once — this copies its files into `overlays/active/`, which is what OBS actually reads.

8. **Point OBS at your overlays** — add a Browser Source for each one, using the URLs shown (and click-to-copy) under **Overlay URLs** on the dashboard, e.g. `http://localhost:5000/overlays/active/starting-soon.html`. These stay the same even after switching scene sets.

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

## Security

By default the server only listens on `127.0.0.1` — nothing else on your network can reach the dashboard or its API. Pass `--lan` (or set `"lan": true` in `config.json`) if you want to check the dashboard from your phone or another device on the same network; the startup banner always states plainly which mode is active. There's no authentication of any kind, so only enable `--lan` on networks you trust.

## Updating

The dashboard shows a banner when a newer version is available on GitHub, with a one-click install (your current files are backed up to `.update-backup/` first; restart afterward to apply). Or from the command line: `python stream-manager.py --check-update` / `--update`.
