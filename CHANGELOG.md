# Changelog

Release notes for each version, newest first. Versions with a
GitHub Release have the same notes there.

## v0.16.0

**Quality-of-life rounds from Oct 22–23**, built and run on foxpc, now in
the repo. No MCP changes; everything in 0.15.0 still applies.

### Chat commands
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

### Quotes
- New `POST /api/quotes/edit` (token-protected). It keeps the quote's id,
  author and date.
- The dashboard quote list gets live search and inline editing.

### Testing from the dashboard
- Quick-test buttons and wheel-outcome tests can run quietly, overlays
  only, with no chat line. That's the default, so a test mid-stream doesn't
  post to chat. Callers that don't send `chat` (the Stream Deck, the MCP
  server) behave as before.
- Test plays, which run as "Dashboard", no longer count toward totals,
  jackpots or the leaderboard. They still show in Recent, tagged as tests.
  Older test plays already in `stats.json` are filtered out when stats are
  read.

### Fixes
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

### Dashboard
- The Log tab can hide "Served: file" lines, which is the default. Real
  events are now kept in their own 100-line buffer, so a burst of static
  files no longer pushes them out.
- Visual and layout pass across the tabs, plus a config editor with a save
  bar for unsaved changes.

### Repo
- `.qol-backup*/` is gitignored. Those are the local backup folders from
  the upgrade rounds.

### Tests
- 319 pass on 3.13, up from 305. On 3.9, 314 pass and 5 SDK-only tests skip.
- New `tests/test_obs_status.py`. New cases in `test_interactive`,
  `test_lifecycle` and `test_wheel` cover the cooldowns, `!commands`,
  quiet tests, stats filtering and teardown order.
- The dashboard browser test passes: 28 of 28.
- The same code has been running on foxpc since Oct 23, merged with 0.15.0
  on Oct 25. All 319 tests pass there too.

## v0.15.0

**Claude can do things now, not just look.** The MCP server gains seven action
tools. Claude asks before each one, the app's own gates still apply, and
every call is logged.

### What Claude can now change

| Tool | Does | Can't |
|---|---|---|
| `sm_shoutout` | Run a real shoutout (card, clip, chat line), like `!so` | Get past the blocklist, cooldowns, repeat guard or queue limit, or pass anything but a valid Twitch username |
| `sm_shoutout_control` | `skip`, `clear`, `on`, `off`, `approve` a waiting raid | Anything else |
| `sm_switch_scene_set` | Switch the overlay theme | Use a set that isn't installed |
| `sm_undo_effects` | Revert short wheel effects (`prism-ctl undo`) | Revoke long ones like a week of VIP |
| `sm_add_quote` | Add a `!quote` as `claude` | Add more than 300 characters |
| `sm_start` | Launch Stream Manager | Start a second copy |
| `sm_restart` | Restart Stream Manager | Restart while you're live, or when it can't tell |

Not included, on purpose: shutdown, installing updates, saving config, Twitch
or Spotify logout, VIP or timeout, and reading chat.

### Fixed: the dashboard's overlay-theme buttons did nothing

`/api/scenes/switch` crashed on every request with an `UnboundLocalError`.
That made the dashboard's theme buttons silent no-ops. The cause was the
clip handler in `do_POST`, which assigned a local called `state`, and in
Python that hides the shared `state` for the whole method. It's renamed
now. One test switches a theme through the endpoint, and another fails if
anything in `do_POST` ever assigns `state` again. This bug was in 0.14.x
and earlier, and the live MCP test caught it.

### Restart guard

- `POST /api/lifecycle/restart` accepts `offline_only: true`. With it set,
  `lifecycle.offline_check` refuses unless it can confirm you're not live.
  That means OBS is not running, or its WebSocket says it isn't streaming.
  It also means Twitch answered `/streams` within the last 30 seconds (or 3
  poll intervals, if that's longer) and said offline. OBS running without
  the WebSocket, Twitch not answering, or a stalled poll loop all count as
  live. Recording doesn't matter, because a restart doesn't touch OBS.
- To support this, the state now carries `obs.ws`, `twitch.checked_at` and
  `server.polled_at`. A failed Helix call used to leave `twitch.live`
  unchanged and indistinguishable from a fresh answer.
- Without the flag, nothing changes. The dashboard, tray and Stream Deck
  can still restart mid-stream.

### Shoutouts

- New `POST /api/shoutout/run` and `POST /api/shoutout/control`. Both are
  token-protected, like every other state-changing endpoint.
- `run` accepts only a Twitch username (3–25 letters, digits or `_`,
  optional `@`), so a URL, a space or a second command can't ride along.
  `source` is allowlisted (`dashboard` or `claude`), so a caller can't pose
  as a raid and skip the raid gates.
- New `shoutout.run()` returns `(ok, reason)`. A refusal names the gate that
  stopped it, for example "pixelwitch was just shouted out; try again in
  about 22s". A success notes when no overlay is listening, so the card
  won't show. `do_shoutout()` still returns a bool, so `!so`, raids and the
  wheel behave exactly as before.

### MCP server

- Action tools carry `readOnlyHint: false`. Restart and undo are also
  marked destructive. Each tool has a typed input schema, and
  `sm_shoutout_control` uses an enum.
- The token is read fresh from `runtime.json` for each call and only goes
  into the `X-SM-Token` header.
- The audit log is `data/mcp-log.jsonl`, one line per action: time, tool,
  arguments (clipped to 200 characters), result. It's opened per entry, so
  the two server copies Claude runs can share it on Windows.
- `sm_start` launches through `Start Stream Manager.bat` (via `os.startfile`)
  so the app never shares the MCP channel's stdout. It then waits up to 25s
  for `/api/ping`.

### Tests

- 305 pass on 3.13, up from 239. On 3.9, 300 pass and 5 SDK-only tests skip.
- New `tests/test_phase2_app.py` (37 tests) covers the guard matrix, the
  endpoints, input vetting, refusal reasons and the theme-switch fix.
- New `tests/test_mcp_actions.py` (29 tests) covers payloads, refusals, the
  audit log, the token never leaking, `sm_start`, and the MCP surface.
- Mutation-checked: breaking any of eight guards fails a test. They are the
  offline flag, trusting OBS without the WebSocket, trusting stale Twitch,
  the token check, the server-side username check, the source allowlist,
  stdout detachment and auditing refusals.
- Checked by hand over real stdio against a live instance: every action
  tool, the refusals, the audit log, the theme switch, and restart being
  refused when Twitch can't confirm you're offline.

## v0.14.1

**Clearer MCP server setup.** Docs and one error message; no behaviour change
for the app or the MCP tools.

- The README now says `python -m pip install -r requirements-mcp.txt`. With
  more than one Python on a PC, bare `pip` can install the SDK into a different
  Python than `mcp-server.bat` runs. The server then quits on every start, and
  Claude only reports "Connection closed".
- When the SDK is missing, the server now prints the exact command to fix it,
  using the full path of the Python it is running under.
- New **If it doesn't connect** note in the README: where Claude logs server
  starts, and that Claude stops retrying after three failed starts.

### Tests

- 239 pass, unchanged in number. The missing-SDK test now also checks that
  the message names the running interpreter.

## v0.14.0

**Claude can read your stream now.** An optional MCP server lets Claude check
OBS, Twitch, the health monitor and preflight, read-only. Nothing changes for
anyone who doesn't set it up.

### MCP server (read-only)

- `python -m stream_manager.mcp_server`, or `mcp-server.bat`, speaks MCP over
  stdio. The Claude desktop app launches it; it opens no port.
- Eight tools: `sm_status`, `sm_preflight`, `sm_health`, `sm_shoutout_status`,
  `sm_scene_sets`, `sm_stream_stats`, `sm_active_effects`, `sm_update_check`.
  All are annotated read-only and idempotent.
- It's a thin client, like `prism-ctl`: it reads `data/runtime.json`, confirms
  the app with `/api/ping`, and calls the existing GET endpoints on `127.0.0.1`.
  Proxy environment variables are ignored for that traffic.
- It never imports `stream_manager.config`. That module prints to stdout (the
  MCP channel) and loads `.env` at import; neither belongs in this process.
- Replies are allowlisted. `sm_status` drops the request log and profile
  image URL; effects drop internal params; update notes are capped at 2,000
  characters.
- `sm_status` answers "not running" as a normal result. The other tools
  return a clear tool error instead of a crash.
- Setup is in the README under **Claude (MCP server)**.

### Requirements

- New optional `requirements-mcp.txt`: `mcp>=2.2,<3` (the official SDK).
  It needs Python 3.10+; the app itself still supports 3.9.

### Updater

- `mcp-server.bat` and `requirements-mcp.txt` join the files the self-updater
  syncs. The updater doing *this* install is the 0.13.x one with the old list,
  so those two files arrive with the next update, or from the release ZIP. The
  README shows a config line that works without the `.bat`.

### Tests

- New `tests/test_mcp_server.py`, 29 tests. They cover runtime discovery,
  stale and foreign runtime files, proxy bypass, allowlisting, no token in
  any request or reply, and import hygiene (no config, no stdout). They also
  cover the full MCP surface through the SDK's in-memory client.
- CI installs the SDK on 3.12. On 3.9 the protocol tests skip and the rest run.
- Checked by hand against a live instance, both in-process and over real stdio.

## v0.13.2

**A sound kit for PRISM Haunt, the Halloween set.** Needs PRISM 2.5.0.

- `fx.js` reads `--fx-kit` from the active set's `redeem-theme.css` on every
  cue, so the sounds change with a set switch, the same way the styling does.
  `prism-haunt` sets it to `haunt`:
  - **tick:** a knock on old wood
  - **spinTick:** a bone rattle
  - **win:** a detuned music box in a minor key
  - **fanfare:** a chapel bell tolling over a D-minor organ swell, with a
    theremin wail on top
  - **lose:** a ghost's moan sinking under a tritone and some wind
  - **hype:** scaled to the event. Raids (and the Risky wheel) get a thunder
    crack and roll, a diminished organ stab and a low bell. Subs and bits get
    the bell and a short organ swell. Follows and first-time chatters get a
    soft chime, so busy chat doesn't turn into a thunderstorm.
- Everything is synthesized with WebAudio, so there are no audio files.
  `?muted` and `?vol=` still apply.
- The kit has its own compressor and reverb. Its peaks match the default kit
  (about 0.07–0.47 at the default volume), so OBS levels don't need touching.
- Every other set, and any set without `--fx-kit`, keeps the default
  chiptune kit, unchanged.
- `hype.html` now passes the event kind: `SMFX.hype(kind)`. The default kit
  ignores it.
- **Wheel:** a set can colour the Risky wheel's rim apart with
  `--wheel-ring-risky`. It is read each spin, so the next Lucky spin gets the
  normal rim back. Sets that don't define it are unchanged.

### Tests
- 210 pass, unchanged.
- Each cue of both kits was rendered offline in headless Chromium, including
  all five hype kinds: no NaN, nothing silent, nothing clipping.
- Both wheel modes were rendered under PRISM Haunt.
- A set without the property falls back to the default kit.

## v0.13.1

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

### Tests

Three new cases (featured preview, Spotify redirect, Spotify client id),
mutation-checked. 210 pass, ruff clean. The widget's preview-then-revert was
checked in headless Chromium, and each set's Starting Soon was rendered with the
widget on top to confirm nothing overlaps.

### Publish

Run `publish_v0131.ps1`. If v0.13.0 was never published, this release carries it too.

## v0.13.0: wheels that actually do things

The Lucky and Risky wheels were half announcements. "VIP for a week" was
permanent, "Pick the next song" had no mechanism, the shoutout went to whoever
redeemed (streamer or not, live or not), JACKPOT bundled things nobody could
deliver, and a third of the Risky wheel only made sense in some games. Manual
outcomes were said once in chat and then forgotten.

### The new wheels

Every segment either happens automatically or goes on the dashboard's **Owed
Rewards** list until it's ticked off. See `INTERACTIVE.md` for the full table.

- **Lucky:** Featured Viewer (avatar on Starting Soon), DJ for a song
  (Spotify link → queue), VIP for 7 days (auto-removed), Chat picks the next
  game (Twitch poll of installed Steam games), Emote party, Name the next run,
  Shoutout (streamers only, live only), JACKPOT.
- **Risky:** Upside-down PNGtuber, Cursed tint, Overlay swap, Slow mode,
  Chat bets (prediction), Silly voice with an on-screen countdown, Bad pun,
  Timeout 60 s, Nothing happens.
- **Costs:** Lucky 1,500 (2 min cooldown), Risky 2,500 (3 min), 2 per viewer
  per stream.

### What makes it safe

- **Eligibility gates.** Each outcome declares what it needs (live, a real
  viewer, Spotify playing, OBS reachable, not a mod…). Segments that fail are
  left off the wheel for that spin, and the overlay draws only what's left.
- **Timed effects** (`timed.py`) snapshot the state they change to disk
  *before* changing it and restore that exact snapshot on their timer, at
  shutdown, or at the next start after a crash. A second hit extends the
  timer instead of stacking; a stacked flip would snapshot the already-flipped
  state and stay upside down forever. **Undo all effects** on the new Wheel tab.
- **OBS writer** (`obs_control.py`) is a separate module from the read-only
  `obs_ws.py` and can only touch the one allowlisted source and its own filter.
- **Rewards pause off-scene** and when Stream Manager closes.
- **Fulfil only after the outcome happens; refund if it fails.** Twitch can't
  refund a redemption that's already been marked fulfilled, so the wheel now
  settles it from the outcome's result, exactly once.

### Fixed

- **Redemptions never passed the viewer's id to the wheel**, so `vip` and
  `timeout:N` actions from a real channel-point spin had no target and silently
  did nothing. Only the mod `!lucky` command path ever worked.
- **New config sections were thrown away on save.** `config.json` keys not in
  `CONFIG_DEFAULTS` are deleted on load and never written. The new `outcomes`,
  `obs_effects`, `steam`, `featured` and `chat` sections are registered.

### Chat (PRISM 2.4.0 overlay)

- **Event cards (phase 5):** subs, resubs, gifts, gift bombs (one card, not
  twenty), raids and cheers render inline as highlighted cards.
- **7TV / BetterTTV / FrankerFaceZ emotes (phase 6):** fetched off the IRC
  thread, cached 30 min, channel emotes beat global ones, each provider fails
  on its own. Toggle with `config.json → chat.third_party_emotes`.

### Stream Deck

- `prism-ctl undo` reverts every active short wheel effect (flip, tint, emote
  only, slow mode, overlay swap) in one key press. VIPs are left alone; revoke
  those from the Wheel tab. Aliases: `undo-all`, `panic`.

### After updating

Re-authorize once: Twitch asks for polls, predictions, chat settings,
announcements and the moderator list; Spotify asks for "add to queue".

### Tests

61 new Python cases (`tests/test_wheel.py`, `tests/test_chat_events.py`) and 7
new browser checks in PRISM's chat overlay harness. Key fixes were
mutation-tested: reverting each one fails at least one case.

## v0.12.1: channel-point redeems wear the active PRISM set

The coin flip / 50-50, Lucky and Risky wheel, slots and hype overlays
(`static/interactive/`) were the last on-stream pieces still locked to the
1.x holo look. They now restyle themselves from
`/overlays/active/redeem-theme.css`, the same way the shoutout card already
follows `shoutout-theme.css`. Switch sets on the dashboard and they follow
within a minute, with no OBS refresh needed. The theme files ship with
PRISM 2.3.0. Holo's is empty, so holo keeps the original look.

### Changes

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

### Fixed

- The hype banner showed `&amp;` for a name containing `&`. The name was
  HTML-escaped and then put into `textContent`, which escapes it again.

### Needs PRISM 2.3.0

With an older PRISM build, the active set has no `redeem-theme.css`. The
overlays then keep the holo look, which is the same as before this release.

## v0.12.0: start, restart and shut down without hunting for a console window

Until now the only way to stop Stream Manager was Ctrl+C in the `.bat` window,
and the only way to restart it was to do that and then double-click the `.bat`
again. This release adds Restart and Shut Down buttons to the dashboard, a
system-tray icon that owns the process, and a `prism-ctl` command a Stream Deck
key can run.

Most of the work was not the buttons.

### The buttons

Overview tab, bottom card. Two clicks each: the first arms, the second fires,
and the armed state expires after five seconds. There is no undo: the second
click stops the thing currently running your stream.

Restarting re-reads `config.json` and reconnects Twitch and OBS. The page keeps
a banner up explaining the gap instead of reporting "connection lost", and
reloads itself when the new process answers, which it has to, because the
session token is regenerated on every start and the old page's token dies with
the old process.

**Restart and Shut Down are local-only.** They need this run's session token
*and* a request from `127.0.0.1`. In `--lan` mode you can still watch the
dashboard from your phone; you cannot stop the stream from it.

If nothing is supervising the process, the Restart button is disabled and the
card says why, rather than quietly turning Restart into Shutdown.

### The tray icon and the Stream Deck key

`Stream Manager (Tray).bat` runs it under a tray icon that spawns the app as a
child and relaunches it on request. Open Dashboard / Restart / Quit. Needs
`pip install pystray pillow`; without them it says so and the launcher's own
restart loop still works.

`prism-ctl.bat status | start | restart | stop` drives a running instance from
any local process. Point a Stream Deck **System → Open** key at it with the
argument `restart`.

A Stream Deck key cannot hold a per-run session token, so `prism-ctl` reads
`data/runtime.json` (port, pid and token, written at startup and removed at
shutdown). That is not a new standing credential: the token's job is to stop a
cross-site page or another device on the LAN from driving the dashboard, and
neither can read a file on this disk. `data/` is gitignored, nothing goes into
`.env`, and the file is written through a temp file and renamed so a half-write
can never be read as "not running".

### Exit code 42 means "restart me"

`Start Stream Manager.bat` is now a loop: it sets `SM_SUPERVISED=1`, runs the
app, and relaunches on exit code 42. Any other code stops, so a crash at startup
breaks the loop instead of spinning. Note the `.bat` files ship with CRLF line
endings deliberately: `cmd.exe` mis-handles `goto` in an LF-only batch file.

### The part that was not the buttons

**Every restart would have come back on the wrong port.** `try_bind_port`
test-bound a bare socket before binding the real server. A bare socket has no
`SO_REUSEADDR`; `_Server` sets it. So the probe was strictly stricter than the
thing it was guarding, and it reported the port taken for any entry sitting in
`TIME_WAIT`, which there always is after an exit, because the dashboard's SSE
stream and every OBS browser source's long poll are connections the server
closes first. `TIME_WAIT` is a minute on Linux and up to four on Windows. Every
restart would have walked to 5001, then 5002, and every overlay pinned to 5000
would have gone blank mid-stream. The probe is gone; the real server binds
directly. This bug predates the release; restart is just what would have made
it happen weekly instead of never.

**The shutdown path now stops the background loops before saving.** Chat,
redeems, EventSub and timers are daemon threads that mutate exactly the state
being written, and they used to keep running through all three saves. A
`!coinflip` landing in that window was recorded into a file that had already
been written, then thrown away. They are stopped first now, each save is
guarded separately, and the whole teardown runs in a `finally`. Previously
only `KeyboardInterrupt` was caught, so any other exception left the port bound
and `runtime.json` stale.

**`cooldowns.json` and the spin history are written atomically**, temp file and
rename, like `stats.py` already did. Truncate-then-write left an empty file if
the process died mid-save, and `load()` then started the stream with no
anti-spam state at all.

**The tray retries the graceful stop instead of reaching for `terminate()`.**
`live_instance()` answers "not running" for a child that is alive but not yet
serving, and startup is several seconds of OBS and Twitch calls, longer on a
first-run device login. Deciding from one failed probe meant hard-killing a
healthy process and losing the state this whole feature exists to protect.

Smaller: `prism-ctl start` no longer launches a rival during the restart gap;
`try_bind_port` reports the port it actually bound; the grace clock starts after
the log write rather than before it.

### Tests

`tests/test_lifecycle.py`: 36 cases covering the action flag, the runtime file,
endpoint auth, the teardown order, the atomic writes, the tray's quit path and
port reacquisition. `tests/dashboard_layout.mjs` grew to 28 browser checks.

Every fix was mutation-tested: revert it, and its test must fail. Two of the
tests failed that check on the first attempt and were rewritten. The port test
originally used a squatter that never accepted a connection, so it left no
`TIME_WAIT` and passed happily against the broken code.

A full independent review of the diff found the port bug, the unstopped loops
and the tray's hard kill. All three are fixed here.

### Known limits

CI runs on Ubuntu. Stream Manager ships on Windows, and several of the fixes
above behave differently there: `TcpTimedWaitDelay` is longer, `terminate()`
is `TerminateProcess` with no cleanup at all, and `os.chmod(0o600)` on
`runtime.json` does nothing because Windows uses the folder's ACL instead. On a
drive-root install another local account could read that file; it holds a
loopback-only token, so the exposure is bounded, but it is not zero.

## v0.11.3: the health banner stops painting a column over the dashboard

Four dashboard defects, found while auditing the repo rather than by anything
breaking. Nothing here touches the overlays, the chat feed or the IRC client.

### The banner rendered as a full-height column

`<body>` is `display: flex` (a row), and `#health-banner` is a direct child of
it. The moment it became visible it turned into a flex **item**: a 344px-wide,
full-height column painted beside the sidebar, shoving the sidebar from `x=22`
to `x=388`. `position: sticky` never took it out of that row.

Its own CSS always described something else: `text-align: center`, and
`border-radius: 0 0 12px 12px`, which only makes sense on a bar hanging off the
top edge. `position: fixed` is what it meant.

**`#conn-banner` had the identical bug.** It was invisible only because it shows
solely when the connection drops. Fixing one and not the other would have put
the same defect back the next time your connection blipped.

This mattered more than it first looked: the banner appears on **any** alert,
including a real one mid-stream, precisely when the dashboard needs to be
readable.

### Opening the dashboard toasted every live alert

`hmSeen` started empty, so the first health sample after a page load treated
everything currently raised as newly raised: a toast each, and a beep for each
`bad` one. That fired on every page load and every refresh, not just at boot.

The first sample is now the baseline. An alert raised *after* it still toasts,
and the tests check both halves.

### A normal launch reported itself as two failures

At `t=0` the Twitch token has not loaded and the IRC client has not finished
CAP + JOIN, so a sample taken immediately found both down. `_update_alerts`
raises on `bad` alone, so that turned an ordinary startup into two raised
alerts (a log entry, a banner, a toast, a beep, and optionally a chat notice)
**every single launch**. Which is how you learn to ignore the banner on the
launch that matters.

There is now a grace window, `health.startup_grace_sec`, default 45 seconds.
Inside it, auth and chat report `warn`; outside it they report `bad` exactly as
before, so a genuinely broken auth is still raised.

### The version chip was hardcoded

`dashboard.html` carried `v0.10.6` as literal text, two releases stale, on the
one element whose entire job is telling you what is running. It now reads
`state.server.version`, which rides along on `/api/status`. The dashboard
already polls that every 2 seconds, so this costs no extra request.

### Tests

The dashboard had **no** browser coverage at all, which is how a banner that is
only visible during an alert shipped broken.

`tests/dashboard_layout.mjs` drives the real dashboard in a real browser against
a stub server and measures the banner. The load-bearing assertion is that
showing it does not move the sidebar: the bug stated as a number rather than as
a screenshot. 12 checks, and a second CI job runs them; the script skips cleanly
when playwright is absent.

`tests/test_health_startup.py` covers the grace window, including that it
expires and that a malformed state does not suppress alerts forever.

Every new test was verified by reverting the fix it covers and confirming it
fails. Reverting the banner fix reproduces the exact geometry: 914px tall,
344px wide, sidebar displaced by 366px.

### Also

`node_modules/` is gitignored **and** excluded from the publish script's
`robocopy /MIR`, which would otherwise mirror an entire npm install into the
clone regardless of `.gitignore`.

## v0.11.2: the README describes what this actually does

Documentation only. No behaviour change, no new code, no migration.

### The chat feed was undocumented

v0.11.0 and v0.11.1 were entirely about the chat feed, and the README never
mentioned it. The Features list covers every other subsystem in detail (the
health monitor gets a paragraph), while the `chat` effects channel,
`GET /api/chat/backfill` and `static/chat/` went undescribed. Reading the repo,
you could not tell the feature existed.

It is listed now, with what comes through it: badges, Twitch emotes, mentions,
replies, cheers, `/me`, and moderation via `CLEARMSG` / `CLEARCHAT`.

### The Security section was wrong

It said, flatly:

> There's no authentication of any kind.

That has not been true for some time. Every state-changing endpoint requires a
session token generated fresh each run and compared with
`secrets.compare_digest`, so a web page you happen to visit cannot POST to the
dashboard. In `--lan` mode, setting `SM_DASHBOARD_PASSWORD` requires HTTP Basic
auth from any non-localhost client, with localhost exempt so OBS on the same PC
still works.

The section now says what guards the server **and** what does not: there is no
TLS, and an unpassworded `--lan` dashboard is readable by anyone who can reach
the port. Overstating the danger is the less harmful error, but it is still an
error. Someone who believes the docs either avoids a safe feature or distrusts
the accurate parts.

### The updater reads Releases, not tags

Now stated in the Updating section, because the distinction has been costing
this project real updates. Tags reached v0.11.1 while the newest published
GitHub **Release** was v0.8.0, so every version since has been invisible to the
in-app updater and no dashboard has offered one.

It fails safe (`_parse_version` does a proper tuple comparison, so an older
"latest" never triggers a downgrade), but the feature advertised in the README
has not worked for five releases. Publishing a Release for each tag from here on
is what fixes it; nothing in the code needed to change.

## v0.11.1: the chat audit pass

An end-to-end audit of the chat feed shipped in v0.11.0, plus the moderation
events the PRISM overlay needs in order to take a deleted message off screen.

Pairs with **PRISM 2.2.0**. Run `python scripts/deploy-chat.py` in the PRISM
repo after updating, then refresh the OBS browser source.

### Fixed

#### The badge fetch blocked the IRC receive thread, on every single message

This is the one that mattered. `_badge_map()` judged staleness on the cache's
own `ok` flag:

```python
fresh = _badge_cache["ok"] and (now - _badge_cache["at"]) < _BADGE_TTL
```

After a failed Helix read `ok` stays `False`, so the result was never considered
fresh and the **next message tried again**: up to two 6-second HTTPS requests,
on the one thread that answers Twitch's `PING` and dispatches every `!command`.
Setting `at` on failure did nothing, because `ok` gated it. A sustained Helix
outage would have stalled chat, frozen commands, and eventually dropped the IRC
connection to a ping timeout.

Three changes:

- **The fetch never happens on the caller's thread.** `_badges()` returns the
  current (possibly empty) map at once and a background thread refreshes it. The
  first messages of a session may render without badge images; that is strictly
  better than an unread socket.
- **Staleness is judged on `at` alone**, so a failure backs off: 60s before the
  first success, the full hour after one (the map already held is still correct,
  so there is nothing to hurry for).
- **The map is replaced, never mutated in place.** `_badges()` iterates the
  object it was handed without holding the lock, so the old dict must stay
  intact. One refresh runs at a time, and the in-flight flag is cleared on every
  exit path, including `BaseException` and a failure to start the thread.
  Latching it would freeze badges for the rest of the session.

`chat.py` calls `chatfeed.warm()` right after the JOIN, which kicks the same
off-thread refresh so the map is usually ready before the first message.

#### An out-of-range emote span ate the rest of the message

`fragments()` clamped a span that ran past the end of the text, which left the
cursor past the end, so the trailing text silently vanished. Spans that do not
land wholly inside the message are dropped instead.

#### `?backfill=25` restored fewer than 25 messages

Moderation events share the chat ring buffer, so slicing the last *n* events
returned fewer and fewer messages the busier the mods had been. `n` now counts
messages: the endpoint walks back until *n* messages are in view and keeps
everything from there on, because the moderation events that follow them have to
replay in order or a deleted message comes back on an OBS source refresh.

#### A malformed moderation payload could take the connection down

`_emit_mod()` wraps the payload **build** as well as the emit. Guarding only the
emit left a bad payload free to kill the IRC thread, which is the whole failure
the guard exists to prevent. A test drives that path.

#### Fonts are served as fonts

`MIME_MAP` gained `woff2`, `woff`, `ttf`, `otf`, plus `gif`, `webp`, `ico`, and
the common audio/video types. Webfonts happened to load as
`application/octet-stream` in CEF, but that is the browser being lenient, not a
contract, and one `nosniff` header away from breaking.

### Added

#### Moderation events

`CLEARMSG` and `CLEARCHAT` are published on the `chat` channel as
`kind: "clearmsg"` and `kind: "clearchat"`. Both arrive on the
`twitch.tv/commands` + `twitch.tv/tags` capabilities already requested, so
nothing new is negotiated.

The line dispatch came out of the read loop into **`chat.dispatch()`** so the
param shapes can be tested directly. A whole-room clear has one param, a
per-user timeout has two, and reading one as the other wipes the overlay. The
first version of that test re-implemented the indexing inside itself and stayed
green through exactly that mistake.

#### `/me` support

Actions arrive CTCP-wrapped. `split_action()` strips the wrapper server-side so
the control characters never reach the renderer, and a new additive `action`
field flags them.

`CONTRACT_VERSION` is **deliberately not bumped.** The renderer refuses any
payload whose `v` it does not equal, so a bump blanks the overlay until both
halves redeploy; an unknown additive field just renders the line upright. That
is the better failure mode when only one side has been deployed.

Twitch does not document whether a `/me` line's emote offsets are measured
against the wrapped string or the body, so `_best_offset()` **detects** it per
message rather than guessing: an emote code is always a whole space-delimited
word, so the origin whose spans land on whole words wins, with a loose fallback
so strictness can never do worse than trusting one reading. Without the word
boundary test, shifting a body-relative span by 8 lands inside the preceding word
whenever that word is 7+ characters: `absolutely Kappa` rendered the emote image
over the middle of `absolutely`.

### Tests

29 new cases in `tests/test_chatfeed.py`, 49 in that file. Every one was
verified by reverting the fix it covers and confirming it fails.

Two of the first drafts were worthless and were rewritten:

- A "never blocks the IRC thread" test asserted on elapsed effect, and passed
  happily against a blocking implementation that merely took 10 seconds. It now
  asserts on the mechanism: a blocking version constructs no `Thread` at all.
- The CLEARCHAT routing test copied `chat.py`'s param expression into itself. It
  now calls `chat.dispatch()`.

### Invariants

These look like obvious simplifications. They are not.

- **Effect ids are monotonic within one run.** The overlay's restart detection is
  built entirely on a reported `last_id` going backwards. Making ids persist
  across restarts silently breaks it.
- **The badge map is replaced, never mutated in place.**
- **The badge in-flight flag is cleared on every exit path.**
- **`chat.py` dispatches CLEARCHAT on param count**: one is a room clear, two is
  one user.

### Also in this release

`chatfeed.py` lost a dead tag-unescape table that nothing referenced.

## v0.11.0: the PRISM chat feed

Stream Manager has held a Twitch IRC connection since v0.2.0, but it only ever
looked at lines starting with the command prefix and dropped everything else.
This release keeps them: every message is normalised and published on a new
`chat` effects channel, so a PRISM overlay can render chat without a second IRC
connection, a second set of credentials, or anything new talking to Twitch.

Nothing renders on stream yet. The overlay that consumes this lands separately;
this release is the feed it reads.

### What's new

**`stream_manager/chatfeed.py`** turns one parsed PRIVMSG into a versioned
message object: user (login, display name, id, colour), resolved badge image
URLs, flags (mod / sub / vip / broadcaster / first-time / returning), bits,
reply parent, the raw text, and an ordered `fragments` list the renderer walks
without doing any index arithmetic of its own.

**A broadcast hook in `chat.py`**, one call at the top of `_handle_privmsg`,
wrapped in a try/except. That thread also dispatches `!commands`; a malformed
message must never take the IRC client down with it.

**`GET /api/chat/backfill?n=25`** returns the newest N messages still in the
ring buffer, oldest first, plus `last_id`. The shared `/api/effects` first poll
deliberately returns no backlog; replaying it once fired an entire session of
shoutouts on a single scene switch (see v0.10.6). Chat wants the opposite after
an OBS browser-source refresh, so it gets its own read rather than changing
behaviour every other overlay depends on.

**Chat is excluded from the dashboard SSE stream** and emits with
`summary=None`. Without both, a busy minute of chat would flood `/api/stream`
and push every shoutout and redeem out of the dashboard's 25-entry activity
feed.

### Two decisions worth recording

**Emote fragments are built server-side, on purpose.** Positions in Twitch's
`emotes` tag are code-point offsets. Python indexes `str` by code point, so the
split is correct by construction. JavaScript indexes by UTF-16 unit, so a
browser doing the same split would land one position early after any astral
character (which is most emoji) and render every later emote over the wrong
text. Doing it here makes that bug structurally impossible instead of something
to remember.

**Chatters with no colour set get a deterministic one** derived from their user
id, drawn from Twitch's own default palette. Without it every uncoloured chatter
shares a single accent and they blur together on screen. The same user always
gets the same colour.

### Reliability

A failed Helix badge fetch costs badges, never a message: the map is cached for
an hour, a failure keeps whatever was already there and backs off, and badges
come back with empty URLs rather than raising. No network call sits between a
message arriving and it reaching the bus.

### Tests

20 new tests in `tests/test_chatfeed.py`: recorded IRC lines in, message
objects out, no network and no live Twitch. They cover plain messages, multiple
and repeated emotes, the emoji-before-emote offset case, out-of-range spans,
emote-only messages, mention detection and the self-mention flag, cheers, replies
with tag unescaping, first-time chatters, missing colour, missing timestamp, and
a badge fetch failure still rendering its message.

75 tests total with the existing suite.

## v0.10.6: shoutout reliability + single instance

### Fixed

#### Overlays replayed the whole session on every scene switch

`GET /api/effects/<channel>?since=0` returned the channel's entire ring buffer,
not just its head id. Every overlay starts at `lastId = 0`, and OBS restarts a
browser source each time its scene becomes active ("Shutdown source when not
visible" is on for all of them), so **switching away to Be Right Back / Starting
Soon / Tech Difficulties and back fired every shoutout, hype alert, wheel spin,
slots roll and coinflip of the session, back to back.**

A first poll now returns `{"events": [], "last_id": <head>}`, so the overlay
starts caught up instead of starting behind. `effects.head()` already existed
for exactly this and had never been wired up. A consumer that already has an id
still gets everything it missed; only a fresh page load skips the backlog, which
is what you want for a transient card.

Covered by `test_effects_first_poll_returns_head_not_backlog`, which drives the
real request handler and fails against the old code.

#### Clips could freeze on screen forever

Ported from PRISM 1.7.1 into `static/interactive/shoutout.html`. A `<video>`
that stalls mid-playback fires neither `error` nor `ended`, so a frozen frame
sat there until the hold timer expired, and a clip that never started at all
held the card for its full duration showing nothing.

The overlay now watches actual playback progress: if a clip hasn't started
within `startMs` (8s) or `currentTime` stops advancing for `stallMs` (3s), it
drops to the thumbnail and shortens the hold. Both values are overridable
per-card.

The PRISM repo's `scripts/test-shoutout-overlay.mjs` now takes a path argument
and is transport-agnostic, so it runs against this overlay too:

    node scripts/test-shoutout-overlay.mjs "<path to>/static/interactive/shoutout.html"

Both overlays pass the same 32 checks.

#### A shoutout with no overlay listening vanished silently

If no overlay is polling the `shoutout` channel (a scene that carries none, or
one whose browser source is shut down), the card had nowhere to go, but the
screen time and repeat guard were still spent, so a retry was refused too. The
service now says so on the console. (Behaviour is unchanged; this is diagnosis,
not a fix.)

#### Launching twice started a second copy instead of focusing the first

`try_bind_port` walks ports 5000–5019 looking for a free one. That is right when
something unrelated holds the default, but it also meant a second launch quietly
started a **rival instance** one port up, with no warning on either console.

The 2026-09-14 health log caught three running at once: three `stream_start`
events within 0.1s of each other, three `Mic/Aux is MUTED` alerts, three
`stream_stop`. Each instance polls OBS, holds its own chat connection, and
writes the same log files, so every event was recorded three times and every
diagnostic was three times noisier than the truth.

Startup now probes `GET /api/ping` across the port range before binding anything.
If a Stream Manager answers, it prints where the running copy is, opens that
dashboard, and exits:

```
● Stream Manager is already running (v0.10.6, PID 24180) on http://localhost:5000
  Opening that dashboard instead of starting a second copy.
  Use --allow-multiple if you really want another instance.
```

The probe distinguishes "another Stream Manager" from "some unrelated program has
this port"; only the former short-circuits startup, so the port-walking fallback
still works as designed. `--allow-multiple` opts out.

Covered by two tests that stand up real HTTP servers: one answering `/api/ping`
as Stream Manager (must be detected), one answering something else (must be
ignored).

### Documented

#### Audio ducking was left out on purpose

`set_clip_playing()` is status only. Its old docstring called it a "hook point
for audio ducking", which is an invitation to rebuild something that was
deliberately dropped. See the long note above it in `stream_manager/shoutout.py`
for the reasoning and, if it's ever revisited, the failure mode to design for
(a failed *restore*, not a failed duck).

### OBS scene changes (done by hand, not in this repo)

- `Stream Ending` and `iPhone` carried only the old hosted GitHub Pages overlay,
  which talks to a WebSocket service that no longer runs. **Neither scene could
  show a shoutout at all**, including raids landing on Stream Ending, which is
  when most of them land. Both now carry the `Interactive Shoutout` source.
- `StreamFox 98 - Game` had the hosted overlay *and* the working one via the
  nested Base scene. The hosted one is hidden there now.
- The hosted `PRISM Shoutout` source is hidden in all three scenes rather than
  deleted, so it is one click to bring back.

### Tests

55 total, run on Windows against Python 3.14.7 (the interpreter this actually
ships on), ruff clean, and `data/` fingerprints verified unchanged across runs so
the suite cannot pollute live diagnostics.

### Note on the tag history

Commit `66b51eb` was pushed by accident on 2026-09-15 carrying these changes
under the previous release's message ("v0.10.5 - the request log was corrupting
itself"), and it force-moved the `v0.10.5` tag off `a178279`. The tag has been
restored to `a178279`, which is the real v0.10.5. `66b51eb` is left in history
rather than rewritten; this release is what describes its contents.

## v0.10.5: the request log was corrupting itself

Found by reading `server.log` from the 2026-09-02 stream end to end rather than
grepping it for the word "error". There were no errors. There were eight torn
lines (entries consisting of a single `l` and a stray carriage return) where
two threads' writes had collided mid-line.

### Fixed

#### Torn log writes
`write_file_log` opened the log fresh on every call and appended with **no lock**,
from every handler thread of a `ThreadingHTTPServer`. During a stream, overlay
polling, the dashboard, SSE and the health monitor all write concurrently. On
Windows the text layer translates `\n` into `\r\n` as a *separate* write, so a
collision leaves a fragment plus a bare carriage return, exactly the damage in
the log.

Every writer now goes through `logging_util.append_line`, which holds a lock for
the whole open-write-close cycle and opens with `newline=""` so the text layer
can't split a line in two. The same unlocked pattern was in `health._log`
(reachable from HTTP threads via `preflight()`) and `shoutout._log_history`
(written from the chat and EventSub threads); both now use the shared helper.

#### Log rotation only happened at startup
The 1 MB ceiling was checked once, at launch. Leaving Stream Manager running
across several streams grew `server.log` without bound: roughly 75 KB per
two-hour stream, so about a day of streaming to reach the ceiling that was never
enforced. Rotation is now checked on every append, and `.old` is removed first so
the rename can't fail on Windows.

### Tests

52 total (2 new), and both were verified against the *old* code to confirm they
actually catch the bug:

- **Rotation** fails without runtime rotation.
- **Concurrency** needed care. Racing 16 threads passes against the buggy code on
  Linux, because `O_APPEND` plus the GIL makes small writes atomic by accident;
  the tearing is a Windows text-mode artifact. The test now forces the same
  window on any platform by splitting each write in two with a yield between.
  Against the old implementation it produces 277 torn lines; against the fix,
  none.

## v0.10.0: the checks that would have caught last night

The 2026-09-02 stream degraded ten separate times, once for thirteen minutes,
and v0.9.0's health monitor would have reported everything green the whole way.
This release closes that gap and fixes the bugs found auditing why.

### The blind spot

Dynamic Bitrate (enabled after the September 1 investigation) does its job: when
the uplink collapses it lowers the bitrate instead of dropping frames. The
September 2 log confirms it worked:

```
Number of lagged frames due to rendering lag/stalls: 40 (0.0%)
(no "dropped frames due to insufficient bandwidth" line at all)
```

Meanwhile the same log shows the stream running at **50 kbps** against a 6000 kbps
target. Zero dropped frames, unwatchable picture. Every check the monitor had was
keyed on drops, so it saw nothing.

### Added

#### Sustained-bitrate check
Compares the live bitrate against the target and warns below 80%, alerts below
50%. The target is learned from the best sustained rate each stream, or pinned
with `health.target_bitrate_kbps`. Catch-up bursts (when a stall clears, OBS
flushes its buffer and one interval measures huge) are ignored so they can't
inflate the learned target. There is a short grace period after going live so
the normal ramp-up doesn't cry wolf.

#### Microphone and audio checks
- **Muted mic while live** is a `bad` alert: the banner, toast and beep fire
  within seconds instead of a viewer telling you twenty minutes in.
- **Mic bound to the Windows default device** is flagged. The September 2 log
  shows `Mic/Aux` following the default and switching between a HyperX Quadcast,
  an Oculus virtual device and an Insta360 receiver as headsets came and went,
  and each switch re-initialises the source, which viewers hear as a dropout.
- **Mid-stream device changes** and other muted audio sources are reported.

Set which input to watch with `health.mic_input` (default `Mic/Aux`), or turn the
whole group off with `health.audio_check`.

#### Actionable OBS WebSocket errors
`obs_ws` swallowed every exception, so an unreachable OBS produced a bare "not
reachable". It now says which failure it was and what to do:

| Failure | What you now see |
|---|---|
| Timeout | `no response from <host>; if OBS is on this PC set OBS_WEBSOCKET_HOST=127.0.0.1 in .env (a stale LAN IP times out like this)` |
| Refused | `connection refused; OBS is not running, or its WebSocket server is off (OBS → Tools → WebSocket Server Settings → Enable)` |
| Bad password | `OBS rejected the password; copy it from Show Connect Info` |
| Bad hostname | `cannot resolve host '<host>'; check OBS_WEBSOCKET_HOST in .env` |

This is what the timeout case was: `OBS_WEBSOCKET_HOST` was pinned to a LAN IP
that the router had long since reassigned.

#### Stream session tracking
Nothing in the app knew when a stream actually started. The monitor now fires on
each offline↔live transition, which resets per-stream state and logs
`stream_start` / `stream_stop` to `data/health-log.jsonl`.

### Fixed

- **First-chat greetings stopped working after the first stream.** `alerts._greeted`
  was never cleared, so leaving Stream Manager running across two streams meant
  every viewer was already "greeted". Now cleared on going live.
- **`cooldowns.json` grew forever.** `_last_user` and `_last_notify` gained a
  permanent entry per (action, viewer) and `save()` wrote all of them. Entries
  older than an hour (long dead, since cooldowns are seconds to minutes) are
  now pruned on use, save and load.
- **`spin-history.json` grew forever** for the same reason; entries older than a
  week are dropped.
- **Race in the overlay heartbeat.** `effects._last_poll` was written from HTTP
  threads and iterated from the monitor thread without a lock, so a new overlay
  connecting mid-iteration could raise `dictionary changed size during iteration`
  and kill the health loop. Both sides now take the existing lock.

### OBS profile changes

- **Audio tracks 1–6: 256 → 160 kbps.** Advanced mode uses `TrackNBitrate`, not
  `AudioBitrate`, so the stream was really sending 6000 video + 256 stream audio
  + 256 VOD audio ≈ **6512 kbps** against a 6000 target. 160 is transparent for
  speech and gameplay and gives back 192 kbps of headroom.
- **Reconnect: 10 retries × 5s → 25 × 2s.** Wi-Fi comes back in seconds; giving
  up after 50 seconds was too eager.

### Tests

50 total (6 new), including a synthetic OBS feed that reproduces the exact
September 2 failure (bitrate collapsing while dropped frames stay at 0.0%) and
asserts the new check catches what the old ones missed.

## v0.9.0: Stream Health Monitor

Last month a stream went out with ~30% dropped frames and nobody knew until
viewers said so in chat. This release makes that impossible to miss: Stream
Manager now watches the numbers that actually predict a bad stream and tells you
the moment they move, while you can still do something about it.

### Added

#### Stream Health Monitor (`stream_manager/health.py`)
Polls OBS over obs-websocket plus Stream Manager's own subsystems every few
seconds and turns them into named checks, each `ok` / `warn` / `bad`.

**What it watches**

| Check | Source | Why it matters |
|---|---|---|
| Dropped frames (network) | `GetStreamStatus.outputSkippedFrames` | Upload can't keep up; the classic "you're lagging" |
| Network congestion | `GetStreamStatus.outputCongestion` | Early warning before drops start |
| Stream reconnecting | `GetStreamStatus.outputReconnecting` | You're off-air right now |
| Rendering lag (GPU) | `GetStats.renderSkippedFrames` | GPU can't compose the scene |
| Encoding lag | `GetStats.outputSkippedFrames` | Encoder is overloaded |
| Bitrate / FPS / frame time | `GetStats` + `GetStreamStatus` | Context for everything above |
| Free disk space | `GetStats.availableDiskSpace` | Recordings about to fail |
| CPU / memory | system poller | The usual suspects |
| Twitch auth · chat · redeems · EventSub | live subsystem state | Silent feature failure |
| Overlay heartbeats | effects long-poll | A browser source that quietly died |

Frame rates are measured **per poll interval**, not cumulatively, so a rough
patch at the start of a stream doesn't mask a healthy hour later, and, more
importantly, a healthy first hour doesn't hide trouble that starts now.

**Alerting**: a check going `bad` raises an alert with hysteresis (it clears
only after several consecutive healthy samples, so a single blip doesn't
flap). Every raise and clear is appended to `data/health-log.jsonl` for
post-stream review, and printed to the console.

#### Dashboard: Health tab
- **Critical banner** across the top of the dashboard whenever anything is bad,
  visible from every tab.
- **Toast + a short beep** the moment an alert is raised (mutable, and rate-limited
  to one beep per 20s), plus a "Recovered" toast when it clears.
- **Live metric tiles** colour-coded by severity, and a full list of checks with
  plain-language messages.
- **Preflight**: one click answers "am I ready to go live?" It runs every check
  that makes sense while offline (auth, chat, EventSub, OBS, overlays, disk,
  CPU/RAM), skips the live-only ones, and treats **OBS not running** as a hard
  blocker rather than a warning.

#### Optional chat alerts
Off by default, since they're viewer-visible. Turn on `health.chat_alert` to
have the bot post a notice (templated, with a cooldown) when the stream goes
bad. Useful if you have mods watching chat but not your second monitor.

### Configuration

New `health` section in `config.json`. Every threshold is editable, and the
Config tab validates it. Defaults:

```json
"health": {
  "enabled": true, "poll_sec": 5,
  "chat_alert": false, "chat_cooldown_sec": 300, "clear_after": 3,
  "dropped_warn": 1.0, "dropped_bad": 3.0,
  "congestion_warn": 0.3, "congestion_bad": 0.6,
  "render_warn": 1.0, "render_bad": 5.0,
  "encode_warn": 1.0, "encode_bad": 5.0,
  "cpu_warn": 85, "cpu_bad": 95, "ram_warn": 90, "ram_bad": 96,
  "disk_warn_mb": 10000, "disk_bad_mb": 2000,
  "overlay_stale_sec": 90, "token_warn_sec": 900
}
```

### API

- `GET /api/health/monitor` returns the current snapshot: checks, metrics, active alerts.
- `GET /api/health/preflight` returns a one-shot readiness report.

### Also in this release

- `obs_ws.fetch_stats()`: `GetStats` + `GetStreamStatus` over a single
  connection, so the monitor costs one round-trip per poll.
- Overlay heartbeat tracking in `effects` (`note_poll` / `subscribers` /
  `last_poll`), which is what makes "your browser source is dead" detectable.
- `validate_section("health", …)` so a bad threshold is rejected with a readable
  message instead of silently breaking the monitor.
- 7 new tests (43 total), covering threshold levels, config fallback, the
  raise/clear hysteresis path with synthetic OBS samples, both preflight
  behaviours, section validation, and heartbeat tracking.

## v0.8.0: Shoutouts built in (PRISM Shoutout merged)

The standalone **PRISM Shoutout** service is now part of Stream Manager. That
retires a second Python process, a second Twitch chat connection, a second
obs-websocket client, a second overlay transport, a duplicated copy of the
Twitch credentials, and an 18 MB virtualenv. Now it's one app, one console, one config.

### Added

#### Shoutouts (`stream_manager/shoutout.py`)
- **`!so @user`** (alias `!shoutout`), mods-only by default, accepting `@name`,
  a bare login, or a full `twitch.tv/...` URL.
- **Automatic raid shoutouts** via EventSub, with a viewer floor, an optional
  allowlist, and an optional "hold until a mod says `!so ok`" approval flow.
- **Mod controls:** `!so skip · clear · off · on · ok · status`.
- **Clip selection, ported intact**: newest clip from the last 7 days (random
  among the newest 8), falling back to a random pick from the most-viewed of the
  last 30 days, skipping the last few clips shown for that streamer so repeat
  shoutouts rotate. Rotation survives restarts via `data/shoutout-log.jsonl`.
- **Signed-MP4 resolution** so clips autoplay without a mature-content gate,
  with the legacy thumbnail fallback for older clips.
- **Safety gates carried over:** blocklist, no self-shoutout, per-login repeat
  guard that outlives a queued card, a screen-time queue model, a lookup guard
  covering the multi-call Helix lookup, and a queue-depth cap.
- **Chat templates** for normal / raid / live / not-found.

#### Overlay
- `static/interactive/shoutout.html`: the PRISM card, re-pointed from its old
  WebSocket service to Stream Manager's effects transport (long-poll, same as
  the other overlays). Reports clip start/stop back to
  `POST /api/shoutout/clip` (the hook for OBS audio ducking).

#### Dashboard & API
- `GET /api/shoutout` status, a **◇ Shoutout** quick-test button, the overlay in
  the copy-able URL list and the live preview switcher, and a `shoutout` section
  in the visual config editor (validated).

### Changed
- `__version__` → `0.8.0`; new `config.json` section: `shoutout` (every value
  optional, defaults carried over from PRISM's tuned settings).
- `twitch.py` gained `invalidate_token()` so Helix calls can retry once on 401.

### Notes
- Uses the Twitch token Stream Manager already holds, so no extra credentials.
- The PRISM **web assets** (scenes, widgets, fonts, branding) are unchanged and
  still served from your OBS assets folder; only the service is retired.
- Not yet ported: OBS **audio ducking** while a clip plays. The overlay already
  reports clip start/stop, so it's a drop-in next step.

## v0.7.1: quiet console on client disconnects

A small patch on top of v0.7.0.

### Fixed
- **No more `ConnectionAbortedError` / `WinError 10053` tracebacks.** When an OBS
  browser source refreshes, a scene changes, or a long-poll / SSE connection is
  torn down, the client aborts the socket mid-response. That's normal, but the
  server was dumping a full traceback each time. The HTTP server now handles the
  connection-reset family (`ConnectionAbortedError`, `ConnectionResetError`,
  `BrokenPipeError`, `TimeoutError`) silently, so the console stays readable
  during a stream.

### Notes
- No functional or configuration changes. Overlays, the dashboard, chat,
  redeems, alerts and Spotify all behave exactly as in v0.7.0.
- This is the change that was already on `main` but landed after the v0.7.0 tag
  was cut; v0.7.1 makes the published release match `main`.

## v0.7.0: Control-center dashboard, real-time, alerts, Spotify & hardening

A major release. The dashboard is rebuilt into a tabbed control center, the
interactive layer gains real-time delivery and new viewer alerts, Spotify
now-playing arrives, and the app gets a security/hardening + packaging pass.
Still standard-library by default; no build step for the app itself.

### Added

#### Dashboard (rebuilt, vanilla JS)
- **Tabbed control center**: Overview · Interactive · Commands · Timers ·
  Quotes · Config · Log, with a connection banner, toast notifications, a
  system-health strip, and remembered last tab.
- **Command Manager**: toggle any built-in command on/off (hand `!coinflip`
  etc. to another bot) and add/edit/delete **custom commands** (`!name →
  response`, with `{user}` / `{args}` / `{1}` variables and everyone/mods
  permission).
- **Visual config editor**: edit command prefix, cooldowns, redeems
  (cost/enabled/prompt), automation, EventSub, viewer alerts, and wheels
  (JSON), saved to `config.json` and applied live with friendly validation.
- **Quotes tab** and **Timers tab**: manage the quote book and timed chat
  messages from the UI.
- **Live overlay preview**: embedded, auto-scaled view of your real overlays.

#### Real-time
- Overlays now receive effects via **long-polling** (near-instant, fewer
  requests) and the dashboard via **Server-Sent Events** (`/api/stream`).

#### Viewer alerts
- **First-time chatter** and **new-follower** alerts fire the hype overlay
  (+ optional chat greeting), toggled in the Config tab.

#### Spotify now-playing
- One-click Spotify connect (Authorization Code + PKCE/secret), a `!song`
  command, and a dashboard widget.

#### Timed messages
- Auto-post chat messages on an interval with a “min lines” anti-spam gate.

### Security & platform
- **Update integrity**: the self-updater only downloads from GitHub over HTTPS
  and logs the archive SHA-256 before validating and installing.
- **CSP** on the dashboard, plus `X-Content-Type-Options` / `Referrer-Policy`.
- **LAN password**: with `--lan` and `SM_DASHBOARD_PASSWORD` set, remote
  devices need the password (localhost/OBS exempt).
- **Cross-platform** OBS process detection via `psutil` (Windows/macOS/Linux).

#### Tooling
- **CI** (GitHub Actions: ruff + pytest on 3.9/3.12), a lint config, and
  **PyInstaller** packaging (`stream-manager.spec` + `build-exe.bat`) for a
  standalone `StreamManager.exe`.

### Changed
- `__version__` → `0.7.0`; new `config.json` sections: `commands`, `timers`,
  `spotify`, `alerts`. New `.env` keys: `SPOTIFY_CLIENT_ID/SECRET`,
  `SM_DASHBOARD_PASSWORD`.
- Frozen (PyInstaller) builds read user data next to the `.exe` and bundled
  assets from the extraction dir.

### Notes
- New-follower alerts need EventSub connected (`websocket-client`) and the
  `moderator:read:followers` scope; you may be asked to re-login once.
- Spotify is optional; add `SPOTIFY_CLIENT_ID` and register
  `http://localhost:5000/auth/spotify/callback` to enable it.

## v0.6.0: One-click login & security hardening

A quality-of-life and safety release. Twitch authorization is now a single
browser click, and the local server and dashboard were hardened against the
issues found in a full review. No new dependencies; still standard-library by
default.

### Added

#### One-click Twitch login
- Switched from the copy-a-code **Device Code** flow to the **Authorization
  Code** flow with a local `/auth/callback` redirect. On first run a browser
  window opens straight to Twitch's consent screen. Click **Authorize** once
  and you're connected. Nothing to copy or paste.
- Works with **either** app type: client secret present → standard secret
  exchange; no secret → **PKCE** automatically.
- **Sign out / re-authorize** button on the dashboard (e.g. to switch to a bot
  account). Manual login link still shown as a fallback for headless setups.

#### Security hardening
- **Session-token gating** on every state-changing endpoint (scene switch,
  update install, config reload, authorize, logout, quotes). The dashboard
  sends a per-run `X-SM-Token`; other sites (CSRF) and other LAN devices can't
  read it, so they can't drive these actions.
- **XSS fixed**: all dynamic text on the dashboard (usernames, overlay labels,
  log lines, leaderboard) is HTML-escaped before rendering.
- **Path-traversal guard rewritten**: file serving now resolves symlinks/`..`
  and confirms the result is genuinely inside the overlay/static root (closes
  the old sibling-prefix edge case; URL-decodes first).
- **Wildcard CORS removed**: the JSON API no longer sends
  `Access-Control-Allow-Origin: *`, so other websites can't read its responses.

#### Quality of life
- Auto-creates a `.env` from `.env.example` on first run if one is missing.
- Startup banner prints the exact **Redirect URL** to register and a clearer
  LAN warning.

### Changed
- `__version__` → `0.6.0`.
- **Default is localhost-only again** (`config.json` → `"lan": false`). If you
  turn `--lan` on, state-changing controls stay locked to the dashboard token.
- `.env.example` / README / INTERACTIVE.md document the redirect URL and the
  one-click flow.
- Narrowed bare `except:` clauses to `except Exception:` so Ctrl-C isn't
  swallowed.

### Notes
- **Register the redirect URL once:** add `http://localhost:5000/auth/callback`
  to your Twitch app's **OAuth Redirect URLs** (the banner shows the exact URL
  if a different port is used). Existing cached tokens keep working; you only
  re-login if you sign out or change scopes.
- No changes to games, wheels, redeems, or overlays; your `config.json`,
  quotes, and stats are untouched.

## v0.5.0: Interactive community layer

A big feature release: viewer-facing games and channel-point redeems with
PRISM-styled OBS overlays, chat integration, and (opt-in) automated outcomes.
All standard-library by default; `websocket-client` is an optional upgrade.

### Added

#### Games & redeems
- **Coin flip**, **50/50**, **slot machine**, **dice**, **magic 8-ball**, and a
  viewer **duel**, via chat commands and/or Twitch channel-point redeems.
- **Lucky Wheel** and **Risky Wheel**: weighted, PRISM-styled canvas overlays.
- **Quote system**: `!quote [n]`, `!addquote`, `!delquote`, `!quotecount`.

#### Overlays (transparent OBS browser sources, 1920×1080)
- `static/interactive/{wheel,coinflip,slots,hype}.html` + shared `fx.js`.
- Synthesized **sound** cues (no audio files); `?muted` / `?vol=` supported.

#### Twitch integration
- **User-token auth** via Device Code Flow (cached + auto-refreshed).
- **Chat** over raw TLS IRC (no dependency), mod/broadcaster-aware rate limiting.
- **Channel points** with Twitch-native limits (global cooldown,
  max-per-user-per-stream), reward **de-dupe by ID**, **refund-on-failure**, and
  **backlog handling** (ignore pre-startup redemptions unless `catch_up`).
- **EventSub** (optional `websocket-client`) for near-instant redemptions and
  **raid / bits / sub** hype, with automatic **polling fallback**.

#### Automation (opt-in, allowlisted)
- Wheel segments can auto **switch scene / grant VIP / shoutout / timeout**.
  No privilege-escalation (`mod`) action, by design.

#### Safety & anti-abuse
- Per-command cooldowns (per-user + global, mods bypass), wheel **no-repeat** and
  **severe-slot cooldown**, all **persisted** across restarts.
- No "temp mod" on the default wheel.

#### Dashboard & ops
- *Interactive* card: device-code login, test buttons, EventSub/automation chips,
  **leaderboard**, recent results, copy-able overlay URLs, and **config hot-reload**.
- New endpoints: `/api/effects/<channel>`, `/api/interactive`,
  `/api/interactive/stats`, `/api/interactive/reload`, `/api/quotes`, and
  `/api/interactive/{test,authorize}`.
- Server upgraded to **ThreadingHTTPServer**.

### Changed
- `__version__` → `0.5.0`.
- `config.json` gains `interactive_enabled`, `command_prefix`, `redeem_poll_interval`,
  `wheels`, `redeems`, `cooldowns`, `automation`, `eventsub`.
- `.env.example` documents the expanded scopes; Twitch app must be **Public**.

### Notes
- Requires re-authorizing once (new scopes).
- Automated VIP/timeout/shoutout need the corresponding scopes; disable via
  `automation.enabled: false` to skip them.
- Runtime data lives in `data/` (gitignored): quotes, stats, reward IDs,
  cooldowns, spin history.

## v0.4.0

### Highlights

**Real OBS status via OBS WebSocket**
- OBS status used to come entirely from `tasklist`/PowerShell, so it could only tell you "is obs64.exe running", nothing about what OBS was actually doing
- Stream Manager now talks to OBS's built-in WebSocket server (protocol v5, OBS 28+) to report the current scene name and actual streaming/recording state
- Falls back automatically to the old process-detection method if the WebSocket isn't reachable or configured, so nothing breaks for anyone who doesn't set it up
- Read-only: it only asks OBS for status, never sends a command that changes anything

**Setup (optional)**
- In OBS: Tools → WebSocket Server Settings → enable it and copy the password
- `pip install websocket-client`, then add `OBS_WEBSOCKET_PASSWORD` (and `OBS_WEBSOCKET_HOST`/`OBS_WEBSOCKET_PORT` if not using the defaults) to `.env` (see `.env.example`)
- The dashboard's OBS card now shows the current scene name and streaming/recording indicators when this is configured

## v0.3.2

### Highlights

**Bind to localhost by default**
- The server used to bind to `0.0.0.0`, meaning anyone on the same network could reach the dashboard and its API (including `/api/update/install` and the scene switcher) with no authentication at all
- It now binds to `127.0.0.1` by default; nothing outside the machine can reach it
- Pass `--lan`, or set `"lan": true` in `config.json`, to explicitly opt back into `0.0.0.0` if you want to check the dashboard from another device on your network. The startup banner always states plainly which mode you're running in

## v0.3.1

### Highlights

**Dashboard UI polish**
- Removed the non-functional "Overlays" / "Settings" sidebar nav items; they had no click handlers and didn't lead anywhere, which read as broken rather than intentional
- Dropped the Google Fonts CDN dependency (`@import url(fonts.googleapis.com/...)`) in favor of a system font stack, so the dashboard no longer needs internet access to render correctly
- Added a responsive breakpoint (`max-width: 760px`): the sidebar and status cards now stack full-width on narrow windows/phones instead of squeezing into unreadable columns
- The request log now skips re-rendering entirely when nothing new has come in, instead of rebuilding the whole list on every 2-second poll

## v0.3.0

### Highlights

**Reorganized into a proper package**
- `stream-manager.py` is now a thin entry point; all logic moved into a `stream_manager/` package (`config`, `console`, `logging_util`, `scenes`, `obs`, `system`, `twitch`, `state`, `updater`, `server`, `cli`)
- Dashboard HTML/CSS/JS moved out of an inline Python string into real files under `static/` (`dashboard.html`, `dashboard.css`, `dashboard.js`), served via a new path-traversal-safe `/static/` route
- No behavior change intended beyond the update mechanism below: same endpoints, same dashboard, same config.json

**Self-update now handles multiple files**
- The old updater only replaced a single `stream-manager.py` asset, which would have left the new `stream_manager/` package stale after an update
- `download_update`/`install_update` now fetch GitHub's auto-generated release source archive (zipball), validate every `.py` file in it, and replace only the app's own paths (`stream-manager.py`, `stream_manager/`, `static/`, `requirements.txt`)
- Backups now live under `.update-backup/` (a directory) instead of a single `.bak` file; `config.json`, `.env`, and logs are never touched
- Backup happens before any files are swapped, so a failure partway through leaves the current install untouched

### Fixes
- Fixed a nested-quote f-string in the startup banner that failed to parse on Python < 3.12

## v0.2.0

### Highlights

**Scene-set switching (modern ⇄ retro)**
- `overlays/modern/`, `overlays/retro/`, and a live `overlays/active/` set that OBS points at permanently
- Switch sets from the dashboard; active set tracked via a `.active-set` manifest with SHA-256 content-hash fallback
- `--setup-scenes` auto-sorts scene HTML into sets by font marker (Perfect DOS VGA 437 → retro, Elder Gods BB → modern), with filename fallback

**Confirm-gated auto-update**
- Checks GitHub Releases for a newer version; download and install are gated behind an explicit confirm
- Validates the download, backs the current file up to `stream-manager.py.bak`, swaps in the staged copy (restart to apply)

### Also since the initial prototype
- Twitch Helix polling via OAuth client-credentials; live status on the dashboard
- Dashboard UI overhaul, OBS uptime tracking, CPU/RAM/GPU monitoring, health endpoint
- Config file with schema validation, file logging, `--poll` flag, port fallback + auto-open browser
- Hardening: path-traversal fix, port-binding race fix
