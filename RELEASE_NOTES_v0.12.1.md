# v0.12.1 — channel-point redeems wear the active PRISM set

The coin flip / 50-50, Lucky and Risky wheel, slots and hype overlays
(`static/interactive/`) were the last on-stream pieces still locked to the
1.x holo look. They now restyle themselves from
`/overlays/active/redeem-theme.css`, the same way the shoutout card already
follows `shoutout-theme.css`. Switch sets on the dashboard and they follow
within a minute, with no OBS refresh needed. The theme files ship with
PRISM 2.3.0. Holo's is empty, so holo keeps the original look.

## Changes

- **Theme loader** in all four overlays. It reads the active set's
  `redeem-theme.css` on load, re-reads it every 60s, and keeps the current
  look if a read fails. `?theme=<url>` overrides it for previewing.
- **The wheel takes its look from the theme.** It is a canvas, which CSS
  cannot reach, so the theme passes `--wheel-*` custom properties (style,
  font, ink, separators, rim, palette, Lucky/Risky accent) and the wheel reads
  them on every spin. A theme palette replaces only the default colours
  `games.py` fills in. A colour you set on a segment in `config.json` is kept.
  The wheel waits up to 400ms for its font, because a canvas never triggers a
  webfont download on its own.
- **Hype accents per event kind** come from `--hype-<kind>` in the theme.
- **No network at load.** The four overlays loaded Google Fonts on every
  OBS refresh. They now use PRISM's vendored
  `/overlays/PRISM/fonts/prism-fonts.css`, like the shoutout card.

## Fixed

- The hype banner showed `&amp;` for a name containing `&`. The name was
  HTML-escaped and then put into `textContent`, which escapes it again.

## Needs PRISM 2.3.0

With an older PRISM build, the active set has no `redeem-theme.css`. The
overlays then keep the holo look, which is the same as before this release.
