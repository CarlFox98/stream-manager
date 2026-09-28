# Stream Manager v0.11.2 — the README describes what this actually does

Documentation only. No behaviour change, no new code, no migration.

## The chat feed was undocumented

v0.11.0 and v0.11.1 were entirely about the chat feed, and the README never
mentioned it. The Features list covers every other subsystem in detail — the
health monitor gets a paragraph — while the `chat` effects channel,
`GET /api/chat/backfill` and `static/chat/` went undescribed. Reading the repo,
you could not tell the feature existed.

It is listed now, with what comes through it: badges, Twitch emotes, mentions,
replies, cheers, `/me`, and moderation via `CLEARMSG` / `CLEARCHAT`.

## The Security section was wrong

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
error — someone who believes the docs either avoids a safe feature or distrusts
the accurate parts.

## The updater reads Releases, not tags

Now stated in the Updating section, because the distinction has been costing
this project real updates. Tags reached v0.11.1 while the newest published
GitHub **Release** was v0.8.0, so every version since has been invisible to the
in-app updater and no dashboard has offered one.

It fails safe — `_parse_version` does a proper tuple comparison, so an older
"latest" never triggers a downgrade — but the feature advertised in the README
has not worked for five releases. Publishing a Release for each tag from here on
is what fixes it; nothing in the code needed to change.
