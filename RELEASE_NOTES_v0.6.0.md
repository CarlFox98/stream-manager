# v0.6.0 — One-click login & security hardening

A quality-of-life and safety release. Twitch authorization is now a single
browser click, and the local server and dashboard were hardened against the
issues found in a full review. No new dependencies; still standard-library by
default.

## Added

### One-click Twitch login
- Switched from the copy-a-code **Device Code** flow to the **Authorization
  Code** flow with a local `/auth/callback` redirect. On first run a browser
  window opens straight to Twitch's consent screen — click **Authorize** once
  and you're connected. Nothing to copy or paste.
- Works with **either** app type: client secret present → standard secret
  exchange; no secret → **PKCE** automatically.
- **Sign out / re-authorize** button on the dashboard (e.g. to switch to a bot
  account). Manual login link still shown as a fallback for headless setups.

### Security hardening
- **Session-token gating** on every state-changing endpoint (scene switch,
  update install, config reload, authorize, logout, quotes). The dashboard
  sends a per-run `X-SM-Token`; other sites (CSRF) and other LAN devices can't
  read it, so they can't drive these actions.
- **XSS fixed** — all dynamic text on the dashboard (usernames, overlay labels,
  log lines, leaderboard) is HTML-escaped before rendering.
- **Path-traversal guard rewritten** — file serving now resolves symlinks/`..`
  and confirms the result is genuinely inside the overlay/static root (closes
  the old sibling-prefix edge case; URL-decodes first).
- **Wildcard CORS removed** — the JSON API no longer sends
  `Access-Control-Allow-Origin: *`, so other websites can't read its responses.

### Quality of life
- Auto-creates a `.env` from `.env.example` on first run if one is missing.
- Startup banner prints the exact **Redirect URL** to register and a clearer
  LAN warning.

## Changed
- `__version__` → `0.6.0`.
- **Default is localhost-only again** (`config.json` → `"lan": false`). If you
  turn `--lan` on, state-changing controls stay locked to the dashboard token.
- `.env.example` / README / INTERACTIVE.md document the redirect URL and the
  one-click flow.
- Narrowed bare `except:` clauses to `except Exception:` so Ctrl-C isn't
  swallowed.

## Notes
- **Register the redirect URL once:** add `http://localhost:5000/auth/callback`
  to your Twitch app's **OAuth Redirect URLs** (the banner shows the exact URL
  if a different port is used). Existing cached tokens keep working — you only
  re-login if you sign out or change scopes.
- No changes to games, wheels, redeems, or overlays; your `config.json`,
  quotes, and stats are untouched.
