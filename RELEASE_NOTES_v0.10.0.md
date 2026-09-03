# v0.10.0 — the checks that would have caught last night

The 2026-09-02 stream degraded ten separate times, once for thirteen minutes,
and v0.9.0's health monitor would have reported everything green the whole way.
This release closes that gap and fixes the bugs found auditing why.

## The blind spot

Dynamic Bitrate (enabled after the September 1 investigation) does its job: when
the uplink collapses it lowers the bitrate instead of dropping frames. The
September 2 log confirms it worked —

```
Number of lagged frames due to rendering lag/stalls: 40 (0.0%)
(no "dropped frames due to insufficient bandwidth" line at all)
```

— while the same log shows the stream running at **50 kbps** against a 6000 kbps
target. Zero dropped frames, unwatchable picture. Every check the monitor had was
keyed on drops, so it saw nothing.

## Added

### Sustained-bitrate check
Compares the live bitrate against the target and warns below 80%, alerts below
50%. The target is learned from the best sustained rate each stream, or pinned
with `health.target_bitrate_kbps`. Catch-up bursts (when a stall clears, OBS
flushes its buffer and one interval measures huge) are ignored so they can't
inflate the learned target. There is a short grace period after going live so
the normal ramp-up doesn't cry wolf.

### Microphone and audio checks
- **Muted mic while live** is a `bad` alert — the banner, toast and beep fire
  within seconds instead of a viewer telling you twenty minutes in.
- **Mic bound to the Windows default device** is flagged. The September 2 log
  shows `Mic/Aux` following the default and switching between a HyperX Quadcast,
  an Oculus virtual device and an Insta360 receiver as headsets came and went —
  each switch re-initialises the source, which viewers hear as a dropout.
- **Mid-stream device changes** and other muted audio sources are reported.

Set which input to watch with `health.mic_input` (default `Mic/Aux`), or turn the
whole group off with `health.audio_check`.

### Actionable OBS WebSocket errors
`obs_ws` swallowed every exception, so an unreachable OBS produced a bare "not
reachable". It now says which failure it was and what to do:

| Failure | What you now see |
|---|---|
| Timeout | `no response from <host> — if OBS is on this PC set OBS_WEBSOCKET_HOST=127.0.0.1 in .env (a stale LAN IP times out like this)` |
| Refused | `connection refused — OBS is not running, or its WebSocket server is off (OBS → Tools → WebSocket Server Settings → Enable)` |
| Bad password | `OBS rejected the password — copy it from Show Connect Info` |
| Bad hostname | `cannot resolve host '<host>' — check OBS_WEBSOCKET_HOST in .env` |

This is what the timeout case was: `OBS_WEBSOCKET_HOST` was pinned to a LAN IP
that the router had long since reassigned.

### Stream session tracking
Nothing in the app knew when a stream actually started. The monitor now fires on
each offline↔live transition, which resets per-stream state and logs
`stream_start` / `stream_stop` to `data/health-log.jsonl`.

## Fixed

- **First-chat greetings stopped working after the first stream.** `alerts._greeted`
  was never cleared, so leaving Stream Manager running across two streams meant
  every viewer was already "greeted". Now cleared on going live.
- **`cooldowns.json` grew forever.** `_last_user` and `_last_notify` gained a
  permanent entry per (action, viewer) and `save()` wrote all of them. Entries
  older than an hour — long dead, since cooldowns are seconds to minutes — are
  now pruned on use, save and load.
- **`spin-history.json` grew forever** for the same reason; entries older than a
  week are dropped.
- **Race in the overlay heartbeat.** `effects._last_poll` was written from HTTP
  threads and iterated from the monitor thread without a lock, so a new overlay
  connecting mid-iteration could raise `dictionary changed size during iteration`
  and kill the health loop. Both sides now take the existing lock.

## OBS profile changes

- **Audio tracks 1–6: 256 → 160 kbps.** Advanced mode uses `TrackNBitrate`, not
  `AudioBitrate`, so the stream was really sending 6000 video + 256 stream audio
  + 256 VOD audio ≈ **6512 kbps** against a 6000 target. 160 is transparent for
  speech and gameplay and gives back 192 kbps of headroom.
- **Reconnect: 10 retries × 5s → 25 × 2s.** Wi-Fi comes back in seconds; giving
  up after 50 seconds was too eager.

## Tests

50 total (6 new), including a synthetic OBS feed that reproduces the exact
September 2 failure — bitrate collapsing while dropped frames stay at 0.0% — and
asserts the new check catches what the old ones missed.
