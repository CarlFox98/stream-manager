# Stream Manager v0.13.2

**A sound kit for PRISM Haunt, the Halloween set.** Needs PRISM 2.5.0.

- `fx.js` reads `--fx-kit` from the active set's `redeem-theme.css` on every
  cue, so the sounds change with a set switch, the same way the styling does.
  `prism-haunt` sets it to `haunt`:
  - **tick:** a knock on old wood
  - **spinTick:** a bone rattle
  - **win:** a detuned music box in a minor key
  - **fanfare:** a chapel bell tolling over a D-minor organ swell, with a
    theremin wail on top
  - **lose:** a ghost's moan sinking under a tritone and some wind
  - **hype:** scaled to the event. Raids (and the Risky wheel) get a thunder
    crack and roll, a diminished organ stab and a low bell. Subs and bits get
    the bell and a short organ swell. Follows and first-time chatters get a
    soft chime, so busy chat doesn't turn into a thunderstorm.
- Everything is synthesized with WebAudio, so there are no audio files.
  `?muted` and `?vol=` still apply.
- The kit has its own compressor and reverb. Its peaks match the default kit
  (about 0.07–0.47 at the default volume), so OBS levels don't need touching.
- Every other set, and any set without `--fx-kit`, keeps the default
  chiptune kit, unchanged.
- `hype.html` now passes the event kind: `SMFX.hype(kind)`. The default kit
  ignores it.
- **Wheel:** a set can colour the Risky wheel's rim apart with
  `--wheel-ring-risky`. It is read each spin, so the next Lucky spin gets the
  normal rim back. Sets that don't define it are unchanged.

## Tests
- 210 pass, unchanged.
- Each cue of both kits was rendered offline in headless Chromium, including
  all five hype kinds: no NaN, nothing silent, nothing clipping.
- Both wheel modes were rendered under PRISM Haunt.
- A set without the property falls back to the default kit.
