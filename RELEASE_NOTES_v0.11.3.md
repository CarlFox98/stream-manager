# Stream Manager v0.11.3 — the health banner stops painting a column over the dashboard

Four dashboard defects, found while auditing the repo rather than by anything
breaking. Nothing here touches the overlays, the chat feed or the IRC client.

## The banner rendered as a full-height column

`<body>` is `display: flex` — a row — and `#health-banner` is a direct child of
it. The moment it became visible it turned into a flex **item**: a 344px-wide,
full-height column painted beside the sidebar, shoving the sidebar from `x=22`
to `x=388`. `position: sticky` never took it out of that row.

Its own CSS always described something else — `text-align: center`, and
`border-radius: 0 0 12px 12px`, which only makes sense on a bar hanging off the
top edge. `position: fixed` is what it meant.

**`#conn-banner` had the identical bug.** It was invisible only because it shows
solely when the connection drops. Fixing one and not the other would have put
the same defect back the next time your connection blipped.

This mattered more than it first looked: the banner appears on **any** alert,
including a real one mid-stream — precisely when the dashboard needs to be
readable.

## Opening the dashboard toasted every live alert

`hmSeen` started empty, so the first health sample after a page load treated
everything currently raised as newly raised: a toast each, and a beep for each
`bad` one. That fired on every page load and every refresh, not just at boot.

The first sample is now the baseline. An alert raised *after* it still toasts —
the tests check both halves.

## A normal launch reported itself as two failures

At `t=0` the Twitch token has not loaded and the IRC client has not finished
CAP + JOIN, so a sample taken immediately found both down. `_update_alerts`
raises on `bad` alone, so that turned an ordinary startup into two raised
alerts — a log entry, a banner, a toast, a beep, and optionally a chat notice —
**every single launch**. Which is how you learn to ignore the banner on the
launch that matters.

There is now a grace window, `health.startup_grace_sec`, default 45 seconds.
Inside it, auth and chat report `warn`; outside it they report `bad` exactly as
before, so a genuinely broken auth is still raised.

## The version chip was hardcoded

`dashboard.html` carried `v0.10.6` as literal text — two releases stale, on the
one element whose entire job is telling you what is running. It now reads
`state.server.version`, which rides along on `/api/status`. The dashboard
already polls that every 2 seconds, so this costs no extra request.

## Tests

The dashboard had **no** browser coverage at all, which is how a banner that is
only visible during an alert shipped broken.

`tests/dashboard_layout.mjs` drives the real dashboard in a real browser against
a stub server and measures the banner. The load-bearing assertion is that
showing it does not move the sidebar — the bug stated as a number rather than as
a screenshot. 12 checks, and a second CI job runs them; the script skips cleanly
when playwright is absent.

`tests/test_health_startup.py` covers the grace window, including that it
expires and that a malformed state does not suppress alerts forever.

Every new test was verified by reverting the fix it covers and confirming it
fails. Reverting the banner fix reproduces the exact geometry: 914px tall,
344px wide, sidebar displaced by 366px.

## Also

`node_modules/` is gitignored **and** excluded from the publish script's
`robocopy /MIR`, which would otherwise mirror an entire npm install into the
clone regardless of `.gitignore`.
