# Stream Manager v0.14.0

**Claude can read your stream now.** An optional MCP server lets Claude check
OBS, Twitch, the health monitor and preflight, read-only. Nothing changes for
anyone who doesn't set it up.

## MCP server (read-only)

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

## Requirements

- New optional `requirements-mcp.txt`: `mcp>=2.2,<3` (the official SDK).
  It needs Python 3.10+; the app itself still supports 3.9.

## Updater

- `mcp-server.bat` and `requirements-mcp.txt` join the files the self-updater
  syncs. The updater doing *this* install is the 0.13.x one with the old list,
  so those two files arrive with the next update, or from the release ZIP. The
  README shows a config line that works without the `.bat`.

## Tests

- New `tests/test_mcp_server.py`, 29 tests. They cover runtime discovery,
  stale and foreign runtime files, proxy bypass, allowlisting, no token in
  any request or reply, and import hygiene (no config, no stdout). They also
  cover the full MCP surface through the SDK's in-memory client.
- CI installs the SDK on 3.12. On 3.9 the protocol tests skip and the rest run.
- Checked by hand against a live instance, both in-process and over real stdio.
