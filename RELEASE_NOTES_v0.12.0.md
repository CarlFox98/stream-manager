# v0.12.0 — start, restart and shut down without hunting for a console window

Until now the only way to stop Stream Manager was Ctrl+C in the `.bat` window,
and the only way to restart it was to do that and then double-click the `.bat`
again. This release adds Restart and Shut Down buttons to the dashboard, a
system-tray icon that owns the process, and a `prism-ctl` command a Stream Deck
key can run.

Most of the work was not the buttons.

## The buttons

Overview tab, bottom card. Two clicks each — the first arms, the second fires,
and the armed state expires after five seconds. There is no undo: the second
click stops the thing currently running your stream.

Restarting re-reads `config.json` and reconnects Twitch and OBS. The page keeps
a banner up explaining the gap instead of reporting "connection lost", and
reloads itself when the new process answers — which it has to, because the
session token is regenerated on every start and the old page's token dies with
the old process.

**Restart and Shut Down are local-only.** They need this run's session token
*and* a request from `127.0.0.1`. In `--lan` mode you can still watch the
dashboard from your phone; you cannot stop the stream from it.

If nothing is supervising the process, the Restart button is disabled and the
card says why, rather than quietly turning Restart into Shutdown.

## The tray icon and the Stream Deck key

`Stream Manager (Tray).bat` runs it under a tray icon that spawns the app as a
child and relaunches it on request. Open Dashboard / Restart / Quit. Needs
`pip install pystray pillow`; without them it says so and the launcher's own
restart loop still works.

`prism-ctl.bat status | start | restart | stop` drives a running instance from
any local process. Point a Stream Deck **System → Open** key at it with the
argument `restart`.

A Stream Deck key cannot hold a per-run session token, so `prism-ctl` reads
`data/runtime.json` — port, pid and token, written at startup and removed at
shutdown. That is not a new standing credential: the token's job is to stop a
cross-site page or another device on the LAN from driving the dashboard, and
neither can read a file on this disk. `data/` is gitignored, nothing goes into
`.env`, and the file is written through a temp file and renamed so a half-write
can never be read as "not running".

## Exit code 42 means "restart me"

`Start Stream Manager.bat` is now a loop: it sets `SM_SUPERVISED=1`, runs the
app, and relaunches on exit code 42. Any other code stops, so a crash at startup
breaks the loop instead of spinning. Note the `.bat` files ship with CRLF line
endings deliberately — `cmd.exe` mis-handles `goto` in an LF-only batch file.

## The part that was not the buttons

**Every restart would have come back on the wrong port.** `try_bind_port`
test-bound a bare socket before binding the real server. A bare socket has no
`SO_REUSEADDR`; `_Server` sets it. So the probe was strictly stricter than the
thing it was guarding, and it reported the port taken for any entry sitting in
`TIME_WAIT` — which there always is after an exit, because the dashboard's SSE
stream and every OBS browser source's long poll are connections the server
closes first. `TIME_WAIT` is a minute on Linux and up to four on Windows. Every
restart would have walked to 5001, then 5002, and every overlay pinned to 5000
would have gone blank mid-stream. The probe is gone; the real server binds
directly. This bug predates the release — restart is just what would have made
it happen weekly instead of never.

**The shutdown path now stops the background loops before saving.** Chat,
redeems, EventSub and timers are daemon threads that mutate exactly the state
being written, and they used to keep running through all three saves. A
`!coinflip` landing in that window was recorded into a file that had already
been written, then thrown away. They are stopped first now, each save is
guarded separately, and the whole teardown runs in a `finally` — previously
only `KeyboardInterrupt` was caught, so any other exception left the port bound
and `runtime.json` stale.

**`cooldowns.json` and the spin history are written atomically**, temp file and
rename, like `stats.py` already did. Truncate-then-write left an empty file if
the process died mid-save, and `load()` then started the stream with no
anti-spam state at all.

**The tray retries the graceful stop instead of reaching for `terminate()`.**
`live_instance()` answers "not running" for a child that is alive but not yet
serving, and startup is several seconds of OBS and Twitch calls — longer on a
first-run device login. Deciding from one failed probe meant hard-killing a
healthy process and losing the state this whole feature exists to protect.

Smaller: `prism-ctl start` no longer launches a rival during the restart gap;
`try_bind_port` reports the port it actually bound; the grace clock starts after
the log write rather than before it.

## Tests

`tests/test_lifecycle.py` — 36 cases covering the action flag, the runtime file,
endpoint auth, the teardown order, the atomic writes, the tray's quit path and
port reacquisition. `tests/dashboard_layout.mjs` grew to 28 browser checks.

Every fix was mutation-tested: revert it, and its test must fail. Two of the
tests failed that check on the first attempt and were rewritten — the port test
originally used a squatter that never accepted a connection, so it left no
`TIME_WAIT` and passed happily against the broken code.

A full independent review of the diff found the port bug, the unstopped loops
and the tray's hard kill. All three are fixed here.

## Known limits

CI runs on Ubuntu. Stream Manager ships on Windows, and several of the fixes
above behave differently there — `TcpTimedWaitDelay` is longer, `terminate()`
is `TerminateProcess` with no cleanup at all, and `os.chmod(0o600)` on
`runtime.json` does nothing because Windows uses the folder's ACL instead. On a
drive-root install another local account could read that file; it holds a
loopback-only token, so the exposure is bounded, but it is not zero.
