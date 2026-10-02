# v0.13.0 — wheels that actually do things

The Lucky and Risky wheels were half announcements. "VIP for a week" was
permanent, "Pick the next song" had no mechanism, the shoutout went to whoever
redeemed (streamer or not, live or not), JACKPOT bundled things nobody could
deliver, and a third of the Risky wheel only made sense in some games. Manual
outcomes were said once in chat and then forgotten.

## The new wheels

Every segment either happens automatically or goes on the dashboard's **Owed
Rewards** list until it's ticked off. See `INTERACTIVE.md` for the full table.

- **Lucky:** Featured Viewer (avatar on Starting Soon), DJ for a song
  (Spotify link → queue), VIP for 7 days (auto-removed), Chat picks the next
  game (Twitch poll of installed Steam games), Emote party, Name the next run,
  Shoutout (streamers only, live only), JACKPOT.
- **Risky:** Upside-down PNGtuber, Cursed tint, Overlay swap, Slow mode,
  Chat bets (prediction), Silly voice with an on-screen countdown, Bad pun,
  Timeout 60 s, Nothing happens.
- **Costs:** Lucky 1,500 (2 min cooldown), Risky 2,500 (3 min), 2 per viewer
  per stream.

## What makes it safe

- **Eligibility gates.** Each outcome declares what it needs (live, a real
  viewer, Spotify playing, OBS reachable, not a mod…). Segments that fail are
  left off the wheel for that spin, and the overlay draws only what's left.
- **Timed effects** (`timed.py`) snapshot the state they change to disk
  *before* changing it and restore that exact snapshot on their timer, at
  shutdown, or at the next start after a crash. A second hit extends the
  timer instead of stacking — a stacked flip would snapshot the already-flipped
  state and stay upside down forever. **Undo all effects** on the new Wheel tab.
- **OBS writer** (`obs_control.py`) is a separate module from the read-only
  `obs_ws.py` and can only touch the one allowlisted source and its own filter.
- **Rewards pause off-scene** and when Stream Manager closes.
- **Fulfil only after the outcome happens; refund if it fails.** Twitch can't
  refund a redemption that's already been marked fulfilled, so the wheel now
  settles it from the outcome's result, exactly once.

## Fixed

- **Redemptions never passed the viewer's id to the wheel**, so `vip` and
  `timeout:N` actions from a real channel-point spin had no target and silently
  did nothing. Only the mod `!lucky` command path ever worked.
- **New config sections were thrown away on save.** `config.json` keys not in
  `CONFIG_DEFAULTS` are deleted on load and never written — the new `outcomes`,
  `obs_effects`, `steam`, `featured` and `chat` sections are registered.

## Chat (PRISM 2.4.0 overlay)

- **Event cards (phase 5):** subs, resubs, gifts, gift bombs (one card, not
  twenty), raids and cheers render inline as highlighted cards.
- **7TV / BetterTTV / FrankerFaceZ emotes (phase 6):** fetched off the IRC
  thread, cached 30 min, channel emotes beat global ones, each provider fails
  on its own. Toggle with `config.json → chat.third_party_emotes`.

## Stream Deck

- `prism-ctl undo` reverts every active short wheel effect (flip, tint, emote
  only, slow mode, overlay swap) in one key press. VIPs are left alone; revoke
  those from the Wheel tab. Aliases: `undo-all`, `panic`.

## After updating

Re-authorize once: Twitch asks for polls, predictions, chat settings,
announcements and the moderator list; Spotify asks for "add to queue".

## Tests

61 new Python cases (`tests/test_wheel.py`, `tests/test_chat_events.py`) and 7
new browser checks in PRISM's chat overlay harness. Key fixes were
mutation-tested: reverting each one fails at least one case.
