/* PRISM interactive overlays — tiny WebAudio sound kit (no audio files).
   Synthesizes cues at runtime so overlays stay self-contained.

   URL params (add to the browser-source URL):
     ?muted        -> no sound
     ?vol=0.5      -> master volume 0..1 (default 0.6)

   Usage:  SMFX.tick(); SMFX.win(); SMFX.lose(); SMFX.fanfare(); SMFX.hype();
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

  window.SMFX = {
    muted: MUTED,
    tick: function () { note(880, 0, 0.05, "square", 0.18); },
    spinTick: function () { note(1200, 0, 0.03, "square", 0.12); },
    win: function () {           // rising arpeggio
      [523, 659, 784, 1047].forEach(function (f, i) { note(f, i * 0.09, 0.18, "triangle", 0.4); });
    },
    fanfare: function () {       // bigger win
      [523, 659, 784, 1047, 1319].forEach(function (f, i) { note(f, i * 0.1, 0.28, "triangle", 0.45); });
      note(1568, 0.5, 0.5, "sine", 0.35);
    },
    lose: function () { slide(320, 120, 0, 0.5, "sawtooth", 0.35); },
    hype: function () {          // whoosh + chord
      slide(200, 900, 0, 0.35, "sawtooth", 0.3);
      [659, 831, 988].forEach(function (f) { note(f, 0.3, 0.5, "triangle", 0.35); });
    }
  };
})();
