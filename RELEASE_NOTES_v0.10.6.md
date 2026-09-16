# Stream Manager v0.10.6 — shoutout reliability + single instance

## Fixed

### Overlays replayed the whole session on every scene switch

`GET /api/effects/<channel>?since=0` returned the channel's entire ring buffer,
not just its head id. Every overlay starts at `lastId = 0`, and OBS restarts a
browser source each time its scene becomes active ("Shutdown source when not
visible" is on for all of them), so **switching away to Be Right Back / Starting
Soon / Tech Difficulties and back fired every shoutout, hype alert, wheel spin,
slots roll and coinflip of the session, back to back.**

A first poll now returns `{"events": [], "last_id": <head>}` — the overlay
starts caught up instead of starting behind. `effects.head()` already existed
for exactly this and had never been wired up. A consumer that already has an id
still gets everything it missed; only a fresh page load skips the backlog, which
is what you want for a transient card.

Covered by `test_effects_first_poll_returns_head_not_backlog`, which drives the
real request handler and fails against the old code.

### Clips could freeze on screen forever

Ported from PRISM 1.7.1 into `static/interactive/shoutout.html`. A `<video>`
that stalls mid-playback fires neither `error` nor `ended`, so a frozen frame
sat there until the hold timer expired — and a clip that never started at all
held the card for its full duration showing nothing.

The overlay now watches actual playback progress: if a clip hasn't started
within `startMs` (8s) or `currentTime` stops advancing for `stallMs` (3s), it
drops to the thumbnail and shortens the hold. Both values are overridable
per-card.

The PRISM repo's `scripts/test-shoutout-overlay.mjs` now takes a path argument
and is transport-agnostic, so it runs against this overlay too:

    node scripts/test-shoutout-overlay.mjs "<path to>/static/interactive/shoutout.html"

Both overlays pass the same 32 checks.

### A shoutout with no overlay listening vanished silently

If no overlay is polling the `shoutout` channel — a scene that carries none, or
one whose browser source is shut down — the card had nowhere to go, but the
screen time and repeat guard were still spent, so a retry was refused too. The
service now says so on the console. (Behaviour is unchanged; this is diagnosis,
not a fix.)

### Launching twice started a second copy instead of focusing the first

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
this port" — only the former short-circuits startup, so the port-walking fallback
still works as designed. `--allow-multiple` opts out.

Covered by two tests that stand up real HTTP servers: one answering `/api/ping`
as Stream Manager (must be detected), one answering something else (must be
ignored).

## Documented

### Audio ducking was left out on purpose

`set_clip_playing()` is status only. Its old docstring called it a "hook point
for audio ducking", which is an invitation to rebuild something that was
deliberately dropped — see the long note above it in `stream_manager/shoutout.py`
for the reasoning and, if it's ever revisited, the failure mode to design for
(a failed *restore*, not a failed duck).

## OBS scene changes (done by hand, not in this repo)

- `Stream Ending` and `iPhone` carried only the old hosted GitHub Pages overlay,
  which talks to a WebSocket service that no longer runs. **Neither scene could
  show a shoutout at all** — including raids landing on Stream Ending, which is
  when most of them land. Both now carry the `Interactive Shoutout` source.
- `StreamFox 98 - Game` had the hosted overlay *and* the working one via the
  nested Base scene. The hosted one is hidden there now.
- The hosted `PRISM Shoutout` source is hidden in all three scenes rather than
  deleted, so it is one click to bring back.

## Tests

55 total, run on Windows against Python 3.14.7 (the interpreter this actually
ships on), ruff clean, and `data/` fingerprints verified unchanged across runs so
the suite cannot pollute live diagnostics.

## Note on the tag history

Commit `66b51eb` was pushed by accident on 2026-09-15 carrying these changes
under the previous release's message ("v0.10.5 - the request log was corrupting
itself"), and it force-moved the `v0.10.5` tag off `a178279`. The tag has been
restored to `a178279`, which is the real v0.10.5. `66b51eb` is left in history
rather than rewritten; this release is what describes its contents.
