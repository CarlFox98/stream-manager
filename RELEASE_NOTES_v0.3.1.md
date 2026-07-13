## Highlights

**Dashboard UI polish**
- Removed the non-functional "Overlays" / "Settings" sidebar nav items — they had no click handlers and didn't lead anywhere, which read as broken rather than intentional
- Dropped the Google Fonts CDN dependency (`@import url(fonts.googleapis.com/...)`) in favor of a system font stack, so the dashboard no longer needs internet access to render correctly
- Added a responsive breakpoint (`max-width: 760px`): the sidebar and status cards now stack full-width on narrow windows/phones instead of squeezing into unreadable columns
- The request log now skips re-rendering entirely when nothing new has come in, instead of rebuilding the whole list on every 2-second poll
