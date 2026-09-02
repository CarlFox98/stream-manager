# v0.7.1 — quiet console on client disconnects

A small patch on top of v0.7.0.

## Fixed
- **No more `ConnectionAbortedError` / `WinError 10053` tracebacks.** When an OBS
  browser source refreshes, a scene changes, or a long-poll / SSE connection is
  torn down, the client aborts the socket mid-response. That's normal, but the
  server was dumping a full traceback each time. The HTTP server now handles the
  connection-reset family (`ConnectionAbortedError`, `ConnectionResetError`,
  `BrokenPipeError`, `TimeoutError`) silently, so the console stays readable
  during a stream.

## Notes
- No functional or configuration changes — overlays, the dashboard, chat,
  redeems, alerts and Spotify all behave exactly as in v0.7.0.
- This is the change that was already on `main` but landed after the v0.7.0 tag
  was cut; v0.7.1 makes the published release match `main`.
