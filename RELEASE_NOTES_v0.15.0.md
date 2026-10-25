# Stream Manager v0.15.0

**Claude can do things now, not just look.** The MCP server gains seven action
tools. Claude asks before each one, the app's own gates still apply, and
every call is logged.

## What Claude can now change

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

## Fixed: the dashboard's overlay-theme buttons did nothing

`/api/scenes/switch` crashed on every request with an `UnboundLocalError`.
That made the dashboard's theme buttons silent no-ops. The cause was the
clip handler in `do_POST`, which assigned a local called `state`, and in
Python that hides the shared `state` for the whole method. It's renamed
now. One test switches a theme through the endpoint, and another fails if
anything in `do_POST` ever assigns `state` again. This bug was in 0.14.x
and earlier, and the live MCP test caught it.

## Restart guard

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

## Shoutouts

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

## MCP server

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

## Tests

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
