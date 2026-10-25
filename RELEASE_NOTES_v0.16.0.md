# Stream Manager v0.16.0

**Quality-of-life rounds from Oct 22–23**, built and run on foxpc, now in
the repo. No MCP changes; everything in 0.15.0 still applies.

## Chat commands
- `!commands` is built from what's actually switched on. It used to be a
  fixed string that never listed custom commands (so `!discord` stayed
  invisible) and kept advertising built-ins after they were turned off
  for another bot.
- Custom commands get optional per-user and global cooldowns, settable on
  the dashboard and capped at an hour. They're silent: an info command
  replying "on cooldown" would be more spam than the repeat it blocks.
  Mods bypass them when `mods_bypass` is on. Leaving a cooldown out of an
  update keeps the old value, so toggling a command on or off can't wipe it.
- The dashboard shows how many times each custom command has been used.

## Quotes
- New `POST /api/quotes/edit` (token-protected). It keeps the quote's id,
  author and date.
- The dashboard quote list gets live search and inline editing.

## Testing from the dashboard
- Quick-test buttons and wheel-outcome tests can run quietly, overlays
  only, with no chat line. That's the default, so a test mid-stream doesn't
  post to chat. Callers that don't send `chat` (the Stream Deck, the MCP
  server) behave as before.
- Test plays, which run as "Dashboard", no longer count toward totals,
  jackpots or the leaderboard. They still show in Recent, tagged as tests.
  Older test plays already in `stats.json` are filtered out when stats are
  read.

## Fixes
- **Wheel stats were never counted.** Spins are recorded as game `wheel`
  with `lucky` or `risky` as the subtype, but the win and outcome tracking
  checked the game name, so Lucky jackpots and wheel outcome counts stayed
  at zero.
- **Restart and Shut Down logged false alerts.** The health monitor kept
  sampling while chat and auth were being stopped, so every restart logged
  "Twitch auth: not authorized" and "chat: disconnected" as real alerts
  that never cleared (7 on Oct 22 alone), and could post a mod alert to
  chat. Health now stops first and waits for an in-flight sample.
- **OBS uptime froze.** With the WebSocket connected, PID and process
  uptime were never refreshed after the first poll ("Uptime: 1s"). They're
  now filled in from psutil on every poll.
- **The wrong GPU was shown.** On a Ryzen 7000 desktop, the integrated
  "AMD Radeon(TM) Graphics" was listed first and shown instead of the RX
  7900 XTX. Discrete GPUs now win over integrated ones.

## Dashboard
- The Log tab can hide "Served: file" lines, which is the default. Real
  events are now kept in their own 100-line buffer, so a burst of static
  files no longer pushes them out.
- Visual and layout pass across the tabs, plus a config editor with a save
  bar for unsaved changes.

## Repo
- `.qol-backup*/` is gitignored. Those are the local backup folders from
  the upgrade rounds.

## Tests
- 319 pass on 3.13, up from 305. On 3.9, 314 pass and 5 SDK-only tests skip.
- New `tests/test_obs_status.py`. New cases in `test_interactive`,
  `test_lifecycle` and `test_wheel` cover the cooldowns, `!commands`,
  quiet tests, stats filtering and teardown order.
- The dashboard browser test passes: 28 of 28.
- The same code has been running on foxpc since Oct 23, merged with 0.15.0
  on Oct 25. All 319 tests pass there too.
