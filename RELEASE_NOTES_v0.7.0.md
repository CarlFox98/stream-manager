# v0.7.0 — Control-center dashboard, real-time, alerts, Spotify & hardening

A major release. The dashboard is rebuilt into a tabbed control center, the
interactive layer gains real-time delivery and new viewer alerts, Spotify
now-playing arrives, and the app gets a security/hardening + packaging pass.
Still standard-library by default; no build step for the app itself.

## Added

### Dashboard (rebuilt, vanilla JS)
- **Tabbed control center**: Overview · Interactive · Commands · Timers ·
  Quotes · Config · Log, with a connection banner, toast notifications, a
  system-health strip, and remembered last tab.
- **Command Manager** — toggle any built-in command on/off (hand `!coinflip`
  etc. to another bot) and add/edit/delete **custom commands** (`!name →
  response`, with `{user}` / `{args}` / `{1}` variables and everyone/mods
  permission).
- **Visual config editor** — edit command prefix, cooldowns, redeems
  (cost/enabled/prompt), automation, EventSub, viewer alerts, and wheels
  (JSON), saved to `config.json` and applied live with friendly validation.
- **Quotes tab** and **Timers tab** — manage the quote book and timed chat
  messages from the UI.
- **Live overlay preview** — embedded, auto-scaled view of your real overlays.

### Real-time
- Overlays now receive effects via **long-polling** (near-instant, fewer
  requests) and the dashboard via **Server-Sent Events** (`/api/stream`).

### Viewer alerts
- **First-time chatter** and **new-follower** alerts fire the hype overlay
  (+ optional chat greeting), toggled in the Config tab.

### Spotify now-playing
- One-click Spotify connect (Authorization Code + PKCE/secret), a `!song`
  command, and a dashboard widget.

### Timed messages
- Auto-post chat messages on an interval with a “min lines” anti-spam gate.

## Security & platform
- **Update integrity**: the self-updater only downloads from GitHub over HTTPS
  and logs the archive SHA-256 before validating and installing.
- **CSP** on the dashboard, plus `X-Content-Type-Options` / `Referrer-Policy`.
- **LAN password**: with `--lan` and `SM_DASHBOARD_PASSWORD` set, remote
  devices need the password (localhost/OBS exempt).
- **Cross-platform** OBS process detection via `psutil` (Windows/macOS/Linux).

### Tooling
- **CI** (GitHub Actions: ruff + pytest on 3.9/3.12), a lint config, and
  **PyInstaller** packaging (`stream-manager.spec` + `build-exe.bat`) for a
  standalone `StreamManager.exe`.

## Changed
- `__version__` → `0.7.0`; new `config.json` sections: `commands`, `timers`,
  `spotify`, `alerts`. New `.env` keys: `SPOTIFY_CLIENT_ID/SECRET`,
  `SM_DASHBOARD_PASSWORD`.
- Frozen (PyInstaller) builds read user data next to the `.exe` and bundled
  assets from the extraction dir.

## Notes
- New-follower alerts need EventSub connected (`websocket-client`) and the
  `moderator:read:followers` scope — you may be asked to re-login once.
- Spotify is optional; add `SPOTIFY_CLIENT_ID` and register
  `http://localhost:5000/auth/spotify/callback` to enable it.
