## Highlights

**Reorganized into a proper package**
- `stream-manager.py` is now a thin entry point; all logic moved into a `stream_manager/` package (`config`, `console`, `logging_util`, `scenes`, `obs`, `system`, `twitch`, `state`, `updater`, `server`, `cli`)
- Dashboard HTML/CSS/JS moved out of an inline Python string into real files under `static/` (`dashboard.html`, `dashboard.css`, `dashboard.js`), served via a new path-traversal-safe `/static/` route
- No behavior change intended beyond the update mechanism below — same endpoints, same dashboard, same config.json

**Self-update now handles multiple files**
- The old updater only replaced a single `stream-manager.py` asset, which would have left the new `stream_manager/` package stale after an update
- `download_update`/`install_update` now fetch GitHub's auto-generated release source archive (zipball), validate every `.py` file in it, and replace only the app's own paths (`stream-manager.py`, `stream_manager/`, `static/`, `requirements.txt`)
- Backups now live under `.update-backup/` (a directory) instead of a single `.bak` file; `config.json`, `.env`, and logs are never touched
- Backup happens before any files are swapped, so a failure partway through leaves the current install untouched

## Fixes
- Fixed a nested-quote f-string in the startup banner that failed to parse on Python < 3.12
