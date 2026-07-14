## Highlights

**Real OBS status via OBS WebSocket**
- OBS status used to come entirely from `tasklist`/PowerShell — it could only tell you "is obs64.exe running", nothing about what OBS was actually doing
- Stream Manager now talks to OBS's built-in WebSocket server (protocol v5, OBS 28+) to report the current scene name and actual streaming/recording state
- Falls back automatically to the old process-detection method if the WebSocket isn't reachable or configured — nothing breaks for anyone who doesn't set it up
- Read-only: it only asks OBS for status, never sends a command that changes anything

**Setup (optional)**
- In OBS: Tools → WebSocket Server Settings → enable it and copy the password
- `pip install websocket-client`, then add `OBS_WEBSOCKET_PASSWORD` (and `OBS_WEBSOCKET_HOST`/`OBS_WEBSOCKET_PORT` if not using the defaults) to `.env` — see `.env.example`
- The dashboard's OBS card now shows the current scene name and streaming/recording indicators when this is configured
