# Stream Manager v0.11.0 — the PRISM chat feed

Stream Manager has held a Twitch IRC connection since v0.2.0, but it only ever
looked at lines starting with the command prefix and dropped everything else.
This release keeps them: every message is normalised and published on a new
`chat` effects channel, so a PRISM overlay can render chat without a second IRC
connection, a second set of credentials, or anything new talking to Twitch.

Nothing renders on stream yet. The overlay that consumes this lands separately;
this release is the feed it reads.

## What's new

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
deliberately returns no backlog — replaying it once fired an entire session of
shoutouts on a single scene switch (see v0.10.6). Chat wants the opposite after
an OBS browser-source refresh, so it gets its own read rather than changing
behaviour every other overlay depends on.

**Chat is excluded from the dashboard SSE stream** and emits with
`summary=None`. Without both, a busy minute of chat would flood `/api/stream`
and push every shoutout and redeem out of the dashboard's 25-entry activity
feed.

## Two decisions worth recording

**Emote fragments are built server-side, on purpose.** Positions in Twitch's
`emotes` tag are code-point offsets. Python indexes `str` by code point, so the
split is correct by construction. JavaScript indexes by UTF-16 unit, so a
browser doing the same split would land one position early after any astral
character — which is most emoji — and render every later emote over the wrong
text. Doing it here makes that bug structurally impossible instead of something
to remember.

**Chatters with no colour set get a deterministic one** derived from their user
id, drawn from Twitch's own default palette. Without it every uncoloured chatter
shares a single accent and they blur together on screen. The same user always
gets the same colour.

## Reliability

A failed Helix badge fetch costs badges, never a message: the map is cached for
an hour, a failure keeps whatever was already there and backs off, and badges
come back with empty URLs rather than raising. No network call sits between a
message arriving and it reaching the bus.

## Tests

20 new tests in `tests/test_chatfeed.py` — recorded IRC lines in, message
objects out, no network and no live Twitch. They cover plain messages, multiple
and repeated emotes, the emoji-before-emote offset case, out-of-range spans,
emote-only messages, mention detection and the self-mention flag, cheers, replies
with tag unescaping, first-time chatters, missing colour, missing timestamp, and
a badge fetch failure still rendering its message.

75 tests total with the existing suite.
