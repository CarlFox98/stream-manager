# v0.10.5 — the request log was corrupting itself

Found by reading `server.log` from the 2026-09-02 stream end to end rather than
grepping it for the word "error". There were no errors. There were eight torn
lines — entries consisting of a single `l` and a stray carriage return — where
two threads' writes had collided mid-line.

## Fixed

### Torn log writes
`write_file_log` opened the log fresh on every call and appended with **no lock**,
from every handler thread of a `ThreadingHTTPServer`. During a stream, overlay
polling, the dashboard, SSE and the health monitor all write concurrently. On
Windows the text layer translates `\n` into `\r\n` as a *separate* write, so a
collision leaves a fragment plus a bare carriage return — exactly the damage in
the log.

Every writer now goes through `logging_util.append_line`, which holds a lock for
the whole open-write-close cycle and opens with `newline=""` so the text layer
can't split a line in two. The same unlocked pattern was in `health._log`
(reachable from HTTP threads via `preflight()`) and `shoutout._log_history`
(written from the chat and EventSub threads); both now use the shared helper.

### Log rotation only happened at startup
The 1 MB ceiling was checked once, at launch. Leaving Stream Manager running
across several streams grew `server.log` without bound — roughly 75 KB per
two-hour stream, so about a day of streaming to reach the ceiling that was never
enforced. Rotation is now checked on every append, and `.old` is removed first so
the rename can't fail on Windows.

## Tests

52 total (2 new), and both were verified against the *old* code to confirm they
actually catch the bug:

- **Rotation** fails without runtime rotation.
- **Concurrency** needed care. Racing 16 threads passes against the buggy code on
  Linux, because `O_APPEND` plus the GIL makes small writes atomic by accident —
  the tearing is a Windows text-mode artifact. The test now forces the same
  window on any platform by splitting each write in two with a yield between.
  Against the old implementation it produces 277 torn lines; against the fix,
  none.
