## Highlights

**Bind to localhost by default**
- The server used to bind to `0.0.0.0`, meaning anyone on the same network could reach the dashboard and its API — including `/api/update/install` and the scene switcher — with no authentication at all
- It now binds to `127.0.0.1` by default; nothing outside the machine can reach it
- Pass `--lan`, or set `"lan": true` in `config.json`, to explicitly opt back into `0.0.0.0` if you want to check the dashboard from another device on your network. The startup banner always states plainly which mode you're running in
