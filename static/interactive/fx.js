/* PRISM interactive overlays: tiny WebAudio sound kit (no audio files).
   Synthesizes cues at runtime so overlays stay self-contained.

   URL params (add to the browser-source URL):
     ?muted        -> no sound
     ?vol=0.5      -> master volume 0..1 (default 0.6)

   Usage:  SMFX.tick(); SMFX.win(); SMFX.lose(); SMFX.fanfare(); SMFX.hype([kind]);
           kind (raid|bits|sub|follow|firstchat) is optional; a kit may vary
           the cue by it. The default kit ignores it.

   Sound kits: the active PRISM set can pick a kit with a CSS custom
   property in its redeem-theme.css, e.g.  :root{ --fx-kit:haunt; }
   It is read on every cue, so a set switch changes the sounds within the
   theme's 60s re-read. Unknown or missing -> the default chiptune kit.
     haunt   creaking knock, bone rattle, music box, church bell + organ,
             ghost moan, thunder (PRISM Haunt, the Halloween set)
*/
(function () {
  "use strict";
  var p = new URLSearchParams(location.search);
  var MUTED = p.has("muted");
  var VOL = Math.max(0, Math.min(parseFloat(p.get("vol")) || 0.6, 1));
  var ctx = null;

  function ac() {
    if (MUTED) return null;
    if (!ctx) {
      try { ctx = new (window.AudioContext || window.webkitAudioContext)(); }
      catch (e) { return null; }
    }
    if (ctx.state === "suspended") { try { ctx.resume(); } catch (e) {} }
    return ctx;
  }

  // one enveloped oscillator note
  function note(freq, start, dur, type, gain) {
    var c = ac(); if (!c) return;
    var t0 = c.currentTime + start;
    var o = c.createOscillator(), g = c.createGain();
    o.type = type || "sine";
    o.frequency.setValueAtTime(freq, t0);
    var peak = (gain == null ? 0.5 : gain) * VOL;
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(peak, t0 + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.connect(g); g.connect(c.destination);
    o.start(t0); o.stop(t0 + dur + 0.02);
  }

  function slide(f1, f2, start, dur, type, gain) {
    var c = ac(); if (!c) return;
    var t0 = c.currentTime + start;
    var o = c.createOscillator(), g = c.createGain();
    o.type = type || "sawtooth";
    o.frequency.setValueAtTime(f1, t0);
    o.frequency.exponentialRampToValueAtTime(Math.max(f2, 1), t0 + dur);
    var peak = (gain == null ? 0.4 : gain) * VOL;
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(peak, t0 + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.connect(g); g.connect(c.destination);
    o.start(t0); o.stop(t0 + dur + 0.02);
  }

  /* ---------- haunt kit ---------- */
  var H = null;   // lazily built: master bus with a compressor and a long, dark reverb
  function hbus(){
    var c = ac(); if (!c) return null;
    if (H && H.c === c) return H;
    var comp = c.createDynamicsCompressor();
    comp.threshold.value = -16; comp.ratio.value = 6; comp.attack.value = 0.004; comp.release.value = 0.25;
    var out = c.createGain(); out.gain.value = VOL;
    comp.connect(out); out.connect(c.destination);
    // impulse: 3.2s of decaying, low-passed noise = a stone chapel
    var len = Math.floor(c.sampleRate * 3.2), ir = c.createBuffer(2, len, c.sampleRate);
    for (var ch = 0; ch < 2; ch++) {
      var d = ir.getChannelData(ch), lp = 0;
      for (var i = 0; i < len; i++) { lp = lp * 0.7 + (Math.random() * 2 - 1) * 0.3; d[i] = lp * Math.pow(1 - i / len, 2.6); }
    }
    var verb = c.createConvolver(); verb.buffer = ir;
    var wet = c.createGain(); wet.gain.value = 0.55;
    verb.connect(wet); wet.connect(comp);
    var nb = c.createBuffer(1, c.sampleRate * 2, c.sampleRate), nd = nb.getChannelData(0);
    for (i = 0; i < nd.length; i++) nd[i] = Math.random() * 2 - 1;
    H = { c: c, dry: comp, verb: verb, noise: nb };
    return H;
  }
  // route a node to dry + reverb send
  function hout(node, send) {
    node.connect(H.dry);
    if (send) { var g = H.c.createGain(); g.gain.value = send; node.connect(g); g.connect(H.verb); }
  }
  function env(g, t0, peak, a, dur) {
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(peak, t0 + a);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
  }
  function htone(freq, start, dur, type, gain, send, attack) {
    if (!hbus()) return null;
    var c = H.c, t0 = c.currentTime + start, o = c.createOscillator(), g = c.createGain();
    o.type = type || "sine"; o.frequency.setValueAtTime(freq, t0);
    env(g, t0, gain, attack || 0.008, dur);
    o.connect(g); hout(g, send); o.start(t0); o.stop(t0 + dur + 0.05);
    return o;
  }
  function hnoise(start, dur, type, freq, q, gain, send, attack) {
    if (!hbus()) return null;
    var c = H.c, t0 = c.currentTime + start, s = c.createBufferSource(), f = c.createBiquadFilter(), g = c.createGain();
    s.buffer = H.noise; s.loop = true;
    f.type = type; f.frequency.setValueAtTime(freq, t0); f.Q.value = q || 0.7;
    env(g, t0, gain, attack || 0.005, dur);
    s.connect(f); f.connect(g); hout(g, send); s.start(t0, Math.random()); s.stop(t0 + dur + 0.05);
    return f;
  }
  function bell(f0, start, gain) {        // inharmonic partials of a cast bell
    [[0.5, 1], [1, 0.8], [1.19, 0.55], [1.5, 0.4], [2, 0.35], [2.74, 0.22], [3.76, 0.12]].forEach(function (p, i) {
      htone(f0 * p[0], start, 4.5 - i * 0.45, "sine", gain * p[1], 0.7, 0.004);
    });
  }
  function organ(freqs, start, dur, gain) {   // slow-swelling, filtered saw chord
    if (!hbus()) return;
    var c = H.c, t0 = c.currentTime + start, f = c.createBiquadFilter(), g = c.createGain();
    f.type = "lowpass"; f.frequency.setValueAtTime(900, t0); f.frequency.linearRampToValueAtTime(1900, t0 + dur * 0.5);
    g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(gain, t0 + 0.25);
    g.gain.setValueAtTime(gain, t0 + dur - 0.6); g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    f.connect(g); hout(g, 0.8);
    freqs.forEach(function (fr) {
      [-6, 6].forEach(function (cents) {
        var o = c.createOscillator(); o.type = "sawtooth"; o.frequency.value = fr; o.detune.value = cents;
        o.connect(f); o.start(t0); o.stop(t0 + dur + 0.05);
      });
    });
  }
  function ghost(f1, f2, start, dur, gain) { // a voice-like moan: vibrato sine through a vowel formant
    if (!hbus()) return;
    var c = H.c, t0 = c.currentTime + start;
    var o = c.createOscillator(), lfo = c.createOscillator(), lg = c.createGain(), bp = c.createBiquadFilter(), g = c.createGain();
    o.type = "triangle"; o.frequency.setValueAtTime(f1, t0);
    o.frequency.linearRampToValueAtTime(f1 * 1.12, t0 + dur * 0.3);
    o.frequency.exponentialRampToValueAtTime(f2, t0 + dur);
    lfo.frequency.value = 5.5; lg.gain.value = f1 * 0.025; lfo.connect(lg); lg.connect(o.frequency);
    bp.type = "bandpass"; bp.frequency.value = 900; bp.Q.value = 1.4;
    g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(gain, t0 + dur * 0.25);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.connect(bp); bp.connect(g); hout(g, 0.9);
    o.start(t0); lfo.start(t0); o.stop(t0 + dur + 0.05); lfo.stop(t0 + dur + 0.05);
  }
  function thunder(start, gain) {
    hnoise(start, 0.18, "highpass", 1800, 0.7, gain * 0.9, 0.5, 0.002);             // the crack
    var f = hnoise(start + 0.04, 3.2, "lowpass", 260, 0.9, gain, 0.6, 0.06);         // the roll
    if (f) { var t = H.c.currentTime + start; f.frequency.setValueAtTime(420, t); f.frequency.exponentialRampToValueAtTime(70, t + 3); }
  }

  var HAUNT = {
    tick: function () {                       // a knock on old wood
      htone(110, 0, 0.12, "sine", 0.55, 0.35, 0.002); htone(165, 0, 0.08, "triangle", 0.2, 0.35, 0.002);
      hnoise(0, 0.05, "bandpass", 1400, 2, 0.25, 0.3, 0.001);
    },
    spinTick: function () {                   // a bone rattle (cheap: fires every ~100ms)
      hnoise(0, 0.04, "bandpass", 1900 + Math.random() * 900, 2.5, 1.4, 0.15, 0.001);
    },
    win: function () {                        // a music box, slightly out of tune, in a minor key
      [1318.5, 1046.5, 880, 987.8, 830.6, 880].forEach(function (f, i) {
        htone(f * (1 + (Math.random() - 0.5) * 0.006), i * 0.16, 1.1, "sine", 0.32, 0.6, 0.003);
        htone(f * 2.01, i * 0.16, 0.4, "sine", 0.06, 0.6, 0.003);
      });
    },
    fanfare: function () {                    // the chapel bell tolls over a D-minor organ swell
      organ([146.8, 174.6, 220, 293.7], 0, 2.6, 0.09);
      bell(220, 0.05, 0.28); bell(220, 1.35, 0.2);
      ghost(660, 880, 0.4, 1.8, 0.07);         // a theremin-ish wail on top
    },
    lose: function () {                       // a ghost moans, sinking
      ghost(520, 190, 0, 1.9, 0.75);
      hnoise(0, 2.2, "bandpass", 500, 0.8, 0.07, 0.6, 0.6);   // wind
      htone(146.8, 0.9, 1.4, "sawtooth", 0.05, 0.6, 0.05);    // tritone under it
      htone(207.7, 0.9, 1.4, "sawtooth", 0.05, 0.6, 0.05);
    },
    hype: function (kind) {                   // scaled to the event, so chat isn't a thunderstorm
      if (kind === "follow" || kind === "firstchat") {      // a soft chime and a breath of wind
        [880, 1318.5, 1046.5].forEach(function (f, i) { htone(f, i * 0.12, 1.4, "sine", 0.22, 0.7, 0.004); });
        hnoise(0, 1.2, "bandpass", 700, 0.8, 0.05, 0.6, 0.3);
      } else if (kind === "bits" || kind === "sub") {       // the chapel bell, a short organ swell
        bell(220, 0, 0.26);
        organ([146.8, 174.6, 220], 0.05, 1.5, 0.07);
      } else {                                               // raid (and anything unknown): thunder
        thunder(0, 0.6);
        organ([138.6, 164.8, 196, 233.1, 277.2], 0.25, 1.9, 0.08);
        bell(110, 0.3, 0.18);
      }
    }
  };
  function kit() {
    try { return getComputedStyle(document.documentElement).getPropertyValue("--fx-kit").trim(); }
    catch (e) { return ""; }
  }
  function cue(name, fallback) {
    return function (arg) { if (MUTED) return; if (kit() === "haunt") return HAUNT[name](arg); fallback(arg); };
  }

  window.SMFX = {
    muted: MUTED,
    tick: cue("tick", function () { note(880, 0, 0.05, "square", 0.18); }),
    spinTick: cue("spinTick", function () { note(1200, 0, 0.03, "square", 0.12); }),
    win: cue("win", function () {           // rising arpeggio
      [523, 659, 784, 1047].forEach(function (f, i) { note(f, i * 0.09, 0.18, "triangle", 0.4); });
    }),
    fanfare: cue("fanfare", function () {   // bigger win
      [523, 659, 784, 1047, 1319].forEach(function (f, i) { note(f, i * 0.1, 0.28, "triangle", 0.45); });
      note(1568, 0.5, 0.5, "sine", 0.35);
    }),
    lose: cue("lose", function () { slide(320, 120, 0, 0.5, "sawtooth", 0.35); }),
    hype: cue("hype", function () {          // whoosh + chord
      slide(200, 900, 0, 0.35, "sawtooth", 0.3);
      [659, 831, 988].forEach(function (f) { note(f, 0.3, 0.5, "triangle", 0.35); });
    }),
    kit: kit
  };
})();
