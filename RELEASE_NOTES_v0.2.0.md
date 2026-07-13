## Highlights

**Scene-set switching (modern ⇄ retro)**
- `overlays/modern/`, `overlays/retro/`, and a live `overlays/active/` set that OBS points at permanently
- Switch sets from the dashboard; active set tracked via a `.active-set` manifest with SHA-256 content-hash fallback
- `--setup-scenes` auto-sorts scene HTML into sets by font marker (Perfect DOS VGA 437 → retro, Elder Gods BB → modern), with filename fallback

**Confirm-gated auto-update**
- Checks GitHub Releases for a newer version; download and install are gated behind an explicit confirm
- Validates the download, backs the current file up to `stream-manager.py.bak`, swaps in the staged copy (restart to apply)

## Also since the initial prototype
- Twitch Helix polling via OAuth client-credentials; live status on the dashboard
- Dashboard UI overhaul, OBS uptime tracking, CPU/RAM/GPU monitoring, health endpoint
- Config file with schema validation, file logging, `--poll` flag, port fallback + auto-open browser
- Hardening: path-traversal fix, port-binding race fix
