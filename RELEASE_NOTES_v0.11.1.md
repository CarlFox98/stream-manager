# Stream Manager v0.11.1 — the chat audit pass

An end-to-end audit of the chat feed shipped in v0.11.0, plus the moderation
events the PRISM overlay needs in order to take a deleted message off screen.

Pairs with **PRISM 2.2.0**. Run `python scripts/deploy-chat.py` in the PRISM
repo after updating, then refresh the OBS browser source.

## Fixed

### The badge fetch blocked the IRC receive thread, on every single message

This is the one that mattered. `_badge_map()` judged staleness on the cache's
own `ok` flag:

```python
fresh = _badge_cache["ok"] and (now - _badge_cache["at"]) < _BADGE_TTL
```

After a failed Helix read `ok` stays `False`, so the result was never considered
fresh and the **next message tried again** — up to two 6-second HTTPS requests,
on the one thread that answers Twitch's `PING` and dispatches every `!command`.
Setting `at` on failure did nothing, because `ok` gated it. A sustained Helix
outage would have stalled chat, frozen commands, and eventually dropped the IRC
connection to a ping timeout.

Three changes:

- **The fetch never happens on the caller's thread.** `_badges()` returns the
  current (possibly empty) map at once and a background thread refreshes it. The
  first messages of a session may render without badge images; that is strictly
  better than an unread socket.
- **Staleness is judged on `at` alone**, so a failure backs off — 60s before the
  first success, the full hour after one (the map already held is still correct,
  so there is nothing to hurry for).
- **The map is replaced, never mutated in place.** `_badges()` iterates the
  object it was handed without holding the lock, so the old dict must stay
  intact. One refresh runs at a time, and the in-flight flag is cleared on every
  exit path — including `BaseException` and a failure to start the thread.
  Latching it would freeze badges for the rest of the session.

`chat.py` calls `chatfeed.warm()` right after the JOIN, which kicks the same
off-thread refresh so the map is usually ready before the first message.

### An out-of-range emote span ate the rest of the message

`fragments()` clamped a span that ran past the end of the text, which left the
cursor past the end — so the trailing text silently vanished. Spans that do not
land wholly inside the message are dropped instead.

### `?backfill=25` restored fewer than 25 messages

Moderation events share the chat ring buffer, so slicing the last *n* events
returned fewer and fewer messages the busier the mods had been. `n` now counts
messages: the endpoint walks back until *n* messages are in view and keeps
everything from there on, because the moderation events that follow them have to
replay in order or a deleted message comes back on an OBS source refresh.

### A malformed moderation payload could take the connection down

`_emit_mod()` wraps the payload **build** as well as the emit. Guarding only the
emit left a bad payload free to kill the IRC thread, which is the whole failure
the guard exists to prevent. A test drives that path.

### Fonts are served as fonts

`MIME_MAP` gained `woff2`, `woff`, `ttf`, `otf`, plus `gif`, `webp`, `ico`, and
the common audio/video types. Webfonts happened to load as
`application/octet-stream` in CEF, but that is the browser being lenient, not a
contract — and one `nosniff` header away from breaking.

## Added

### Moderation events

`CLEARMSG` and `CLEARCHAT` are published on the `chat` channel as
`kind: "clearmsg"` and `kind: "clearchat"`. Both arrive on the
`twitch.tv/commands` + `twitch.tv/tags` capabilities already requested, so
nothing new is negotiated.

The line dispatch came out of the read loop into **`chat.dispatch()`** so the
param shapes can be tested directly. A whole-room clear has one param, a
per-user timeout has two, and reading one as the other wipes the overlay — the
first version of that test re-implemented the indexing inside itself and stayed
green through exactly that mistake.

### `/me` support

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
whenever that word is 7+ characters — `absolutely Kappa` rendered the emote image
over the middle of `absolutely`.

## Tests

29 new cases in `tests/test_chatfeed.py`, 49 in that file. Every one was
verified by reverting the fix it covers and confirming it fails.

Two of the first drafts were worthless and were rewritten:

- A "never blocks the IRC thread" test asserted on elapsed effect, and passed
  happily against a blocking implementation that merely took 10 seconds. It now
  asserts on the mechanism — a blocking version constructs no `Thread` at all.
- The CLEARCHAT routing test copied `chat.py`'s param expression into itself. It
  now calls `chat.dispatch()`.

## Invariants

These look like obvious simplifications. They are not.

- **Effect ids are monotonic within one run.** The overlay's restart detection is
  built entirely on a reported `last_id` going backwards. Making ids persist
  across restarts silently breaks it.
- **The badge map is replaced, never mutated in place.**
- **The badge in-flight flag is cleared on every exit path.**
- **`chat.py` dispatches CLEARCHAT on param count**: one is a room clear, two is
  one user.

## Also in this release

`chatfeed.py` lost a dead tag-unescape table that nothing referenced.
