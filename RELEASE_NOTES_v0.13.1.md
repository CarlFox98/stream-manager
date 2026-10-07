# Stream Manager v0.13.1

Fixes found on the first real look at v0.13.0 in OBS.

- **Featured Viewers no longer cover the Starting Soon layout.** The widget is
  now a row (or column) of compact avatar chips, and each PRISM set places it in
  space its own Starting Soon leaves empty: under the Channels/Followers column
  on Signal, under the countdown on Soft, right of the timer on Holo. Placement
  comes from the set's `redeem-theme.css` (`--feat-top/-right/-left/-dir/...`),
  so it follows a set switch like the rest of the styling. Needs PRISM 2.4.1.
- **Testing Featured Viewer from the dashboard no longer saves a "Dashboard"
  card.** A test shows the card for 15 seconds, then the saved list returns.
  Real winners are saved as before.

- **Spotify could never connect.** Spotify stopped accepting `localhost`
  redirect URIs in 2025, and Stream Manager still sent
  `http://localhost:5000/...`. It now uses `http://127.0.0.1:5000/auth/spotify/callback`,
  the address Stream Manager actually listens on. Register that exact URI in
  your Spotify app.
- **Spotify client id can live in config.json** (`"spotify": {"client_id": "..."}`).
  The id is public; it's already inside every PKCE page that uses it. `.env`
  still wins if set, and the client secret stays `.env`-only.

## Tests

Three new cases (featured preview, Spotify redirect, Spotify client id),
mutation-checked. 210 pass, ruff clean. The widget's preview-then-revert was
checked in headless Chromium, and each set's Starting Soon was rendered with the
widget on top to confirm nothing overlaps.

## Publish

Run `publish_v0131.ps1`. If v0.13.0 was never published, this release carries it too.
