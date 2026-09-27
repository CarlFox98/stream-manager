"""Contract tests for the PRISM chat feed (stream_manager/chatfeed.py).

Recorded IRC lines in, message objects out. No network, no live Twitch: the
badge map is stubbed, so every test here is pure logic.

Run from the repo root:   python -m pytest -q
"""
import os
import re

import pytest

from stream_manager import chat as chatmod, chatfeed


# Captured at import, before the autouse fixture stubs it out.
_REAL_BADGE_MAP = chatfeed._badge_map


def line(tags, text, nick="neothefox98"):
    """Build one raw PRIVMSG the way Twitch sends it."""
    tagstr = ";".join(f"{k}={v}" for k, v in tags.items())
    host = f":{nick}!{nick}@{nick}.tmi.twitch.tv"
    return f"@{tagstr} {host} PRIVMSG #neothefox98 :{text}"


def build(tags, text, nick="neothefox98"):
    parsed_tags, prefix, command, params = chatmod._parse(line(tags, text, nick))
    assert command == "PRIVMSG"
    return chatfeed.message(parsed_tags, prefix, params[-1])


BASE = {
    "badges": "", "color": "#5CF2E3", "display-name": "NeoTheFox98",
    "emotes": "", "first-msg": "0", "id": "msg-1", "mod": "0",
    "subscriber": "0", "tmi-sent-ts": "1700000000000", "user-id": "12345",
}


@pytest.fixture(autouse=True)
def _stub_badges(monkeypatch):
    """No network: a fixed badge map, and a known broadcaster login."""
    monkeypatch.setattr(chatfeed, "_badge_map", lambda force=False: {
        "broadcaster/1": {"url": "https://cdn/broadcaster.png", "title": "Broadcaster"},
        "subscriber/12": {"url": "https://cdn/sub12.png", "title": "1-Year Subscriber"},
    })
    monkeypatch.setattr(chatfeed, "_self_login", lambda: "neothefox98")
    yield


def texts(msg):
    return [f for f in msg["fragments"] if f["type"] == "text"]


def emotes(msg):
    return [f for f in msg["fragments"] if f["type"] == "emote"]


# ------------------------------------------------------------------ basics --
def test_plain_message():
    m = build(BASE, "hello chat")
    assert m["v"] == chatfeed.CONTRACT_VERSION
    assert m["kind"] == "msg"
    assert m["id"] == "msg-1"
    assert m["ts"] == 1700000000000
    assert m["user"] == {"login": "neothefox98", "name": "NeoTheFox98",
                         "id": "12345", "color": "#5CF2E3"}
    assert m["text"] == "hello chat"
    assert m["fragments"] == [{"type": "text", "text": "hello chat"}]
    assert m["bits"] == 0
    assert m["reply_to"] is None
    assert m["mentions"] == []


def test_display_name_falls_back_to_nick():
    tags = {**BASE, "display-name": ""}
    assert build(tags, "hi")["user"]["name"] == "neothefox98"


def test_flags_from_badges_and_tags():
    tags = {**BASE, "badges": "broadcaster/1,subscriber/12", "mod": "0"}
    m = build(tags, "hi")
    assert m["flags"]["broadcaster"] is True
    assert m["flags"]["sub"] is True
    assert m["flags"]["mod"] is False
    assert m["flags"]["vip"] is False


def test_badges_resolve_to_urls():
    tags = {**BASE, "badges": "broadcaster/1,subscriber/12"}
    b = build(tags, "hi")["badges"]
    assert [x["set"] for x in b] == ["broadcaster", "subscriber"]
    assert b[0]["url"] == "https://cdn/broadcaster.png"
    assert b[1]["title"] == "1-Year Subscriber"


# ------------------------------------------------------------------ emotes --
def test_two_emotes_split_in_order():
    # "Kappa hi Kappa" -> Kappa at 0-4 and 9-13
    tags = {**BASE, "emotes": "25:0-4,9-13"}
    m = build(tags, "Kappa hi Kappa")
    assert [f["type"] for f in m["fragments"]] == ["emote", "text", "emote"]
    assert emotes(m)[0]["id"] == "25"
    assert emotes(m)[0]["name"] == "Kappa"
    assert emotes(m)[0]["url"].endswith("/25/default/dark/2.0")
    assert texts(m)[0]["text"] == " hi "


def test_two_different_emotes():
    tags = {**BASE, "emotes": "25:0-4/1902:6-10"}
    m = build(tags, "Kappa Keepo")
    assert [f.get("id") for f in emotes(m)] == ["25", "1902"]
    assert [f["name"] for f in emotes(m)] == ["Kappa", "Keepo"]


def test_emoji_before_emote_uses_code_point_offsets():
    """The regression that motivated server-side fragment building.

    U+1F389 is one code point but two UTF-16 units, so a browser splitting on
    JS string indices would slice one character early and render the emote over
    the wrong text. Python indexes by code point, so the span is exact.
    """
    text = "\U0001F389 Kappa"          # party popper, space, Kappa
    assert text[2:7] == "Kappa"        # code points 2..6
    tags = {**BASE, "emotes": "25:2-6"}
    m = build(tags, text)
    assert emotes(m)[0]["name"] == "Kappa"
    assert texts(m)[0]["text"] == "\U0001F389 "
    assert len(emotes(m)) == 1


def test_out_of_range_emote_span_is_skipped():
    tags = {**BASE, "emotes": "25:40-44"}
    m = build(tags, "short")
    assert emotes(m) == []
    assert m["fragments"] == [{"type": "text", "text": "short"}]


def test_emote_only_message_has_no_empty_text_fragments():
    tags = {**BASE, "emotes": "25:0-4"}
    m = build(tags, "Kappa")
    assert m["fragments"] == [{
        "type": "emote", "id": "25", "name": "Kappa",
        "url": chatfeed.EMOTE_CDN.format(id="25")}]


# ---------------------------------------------------------------- mentions --
def test_mention_of_broadcaster_is_flagged_self():
    m = build(BASE, "hey @NeoTheFox98 nice run")
    men = [f for f in m["fragments"] if f["type"] == "mention"]
    assert len(men) == 1
    assert men[0]["login"] == "neothefox98"
    assert men[0]["self"] is True
    assert m["mentions"] == ["neothefox98"]


def test_mention_of_someone_else_is_not_self():
    m = build(BASE, "@someone_else hi")
    men = [f for f in m["fragments"] if f["type"] == "mention"]
    assert men[0]["self"] is False


def test_mentions_are_not_scanned_inside_emote_spans():
    tags = {**BASE, "emotes": "25:0-4"}
    m = build(tags, "Kappa @NeoTheFox98")
    assert [f["type"] for f in m["fragments"]] == ["emote", "text", "mention"]


# ------------------------------------------------------------- bits, reply --
def test_cheer_carries_bits():
    tags = {**BASE, "bits": "100"}
    m = build(tags, "Cheer100 go go go")
    assert m["bits"] == 100


def test_bad_bits_value_defaults_to_zero():
    tags = {**BASE, "bits": "lots"}
    assert build(tags, "hi")["bits"] == 0


def test_reply_is_populated_and_unescaped():
    tags = {**BASE,
            "reply-parent-msg-id": "parent-9",
            "reply-parent-display-name": "SomeOne",
            "reply-parent-msg-body": r"hello\sthere\syou"}
    m = build(tags, "@SomeOne yes")
    assert m["reply_to"] == {"id": "parent-9", "name": "SomeOne",
                             "text": "hello there you"}


# -------------------------------------------------------------- edge cases --
def test_first_time_chatter_flag():
    assert build({**BASE, "first-msg": "1"}, "hi")["flags"]["first"] is True
    assert build(BASE, "hi")["flags"]["first"] is False


def test_missing_colour_is_deterministic_per_user():
    a = build({**BASE, "color": ""}, "hi")
    b = build({**BASE, "color": ""}, "again")
    assert a["user"]["color"] == b["user"]["color"]
    assert a["user"]["color"].startswith("#")
    other = build({**BASE, "color": "", "user-id": "99999"}, "hi")
    assert other["user"]["color"] in chatfeed._FALLBACK_COLORS


def test_missing_timestamp_falls_back_to_now():
    m = build({**BASE, "tmi-sent-ts": ""}, "hi")
    assert m["ts"] > 1_600_000_000_000


@pytest.fixture
def badge_cache(monkeypatch):
    """A clean, un-stubbed badge cache with no network underneath it."""
    monkeypatch.setattr(chatfeed, "_badge_map", _REAL_BADGE_MAP)
    monkeypatch.setattr(chatfeed, "_badge_cache", {"map": {}, "at": 0.0, "ok": False})
    monkeypatch.setattr(chatfeed, "_badge_refreshing", False)
    yield


def test_badge_fetch_failure_still_renders_the_message(monkeypatch, badge_cache):
    """A dead Helix call must cost badges, never the message."""
    monkeypatch.setattr(chatfeed, "_build_badge_map",
                        lambda: (_ for _ in ()).throw(OSError("helix down")))
    # Run the refresh inline so the assertion is deterministic instead of
    # racing a daemon thread.
    monkeypatch.setattr(chatfeed.threading, "Thread", _InlineThread)

    m = build({**BASE, "badges": "broadcaster/1"}, "still here")
    assert m["text"] == "still here"
    assert m["badges"] == [{"set": "broadcaster", "version": "1",
                            "url": "", "title": "broadcaster"}]


class _InlineThread:
    """threading.Thread stand-in that runs the target on .start()."""

    def __init__(self, target=None, name=None, daemon=None, args=(), kwargs=None):
        self._t, self._a, self._k = target, args, kwargs or {}

    def start(self):
        if self._t:
            self._t(*self._a, **self._k)

    def join(self, timeout=None):
        pass


def test_failed_badge_fetch_backs_off_instead_of_retrying_every_message(
        monkeypatch, badge_cache):
    """The regression that stalled the IRC thread.

    Staleness used to be gated on the cache's `ok` flag, so a failed fetch was
    never considered fresh and EVERY subsequent message launched two more 6s
    Helix calls — on the socket-reading thread.
    """
    calls = []

    def boom():
        calls.append(1)
        raise OSError("helix down")

    monkeypatch.setattr(chatfeed, "_build_badge_map", boom)
    monkeypatch.setattr(chatfeed.threading, "Thread", _InlineThread)

    for _ in range(5):
        build({**BASE, "badges": "broadcaster/1"}, "hi")
    assert len(calls) == 1, "a failed fetch must back off, not retry per message"

    # ...and it DOES retry once the retry window has passed.
    chatfeed._badge_cache["at"] -= chatfeed._BADGE_RETRY + 1
    build({**BASE, "badges": "broadcaster/1"}, "hi")
    assert len(calls) == 2


class _Recorder:
    """threading.Thread stand-in that records starts and runs NOTHING."""

    def __init__(self, target=None, name=None, daemon=None, args=(), kwargs=None):
        self.name = name
        _Recorder.started.append(name)

    def start(self):
        _Recorder.launched.append(self.name)

    started = []
    launched = []


@pytest.fixture
def recorder(monkeypatch):
    _Recorder.started, _Recorder.launched = [], []
    monkeypatch.setattr(chatfeed.threading, "Thread", _Recorder)
    yield _Recorder


def test_badge_refresh_happens_off_thread(monkeypatch, badge_cache, recorder):
    """_badges() runs on the IRC receive thread — the one that answers Twitch's
    PING and dispatches every !command. A Helix read there costs up to 12s of
    unread socket, i.e. a ping timeout and a dropped connection mid-stream.

    Asserted on the MECHANISM, not on elapsed time: a blocking implementation
    constructs no Thread at all, and a wall-clock assertion passed happily
    against one that simply took 10 seconds.
    """
    monkeypatch.setattr(
        chatfeed, "_build_badge_map",
        lambda: pytest.fail("badge fetch ran on the caller's thread"))

    m = build({**BASE, "badges": "broadcaster/1"}, "hi")
    assert recorder.launched == ["prism-badges"]
    assert m["badges"][0]["url"] == ""          # rendered without waiting


def test_only_one_badge_refresh_runs_at_a_time(monkeypatch, badge_cache, recorder):
    """The in-flight flag: four messages in a row while a refresh is parked must
    not spawn four Helix reads."""
    monkeypatch.setattr(chatfeed, "_build_badge_map", dict)
    for _ in range(4):
        build({**BASE, "badges": "broadcaster/1"}, "hi")
    assert recorder.launched == ["prism-badges"]


def test_a_refresh_killed_mid_flight_does_not_latch_the_flag(monkeypatch, badge_cache):
    """If the flag survived a thread dying, badges would never refresh again for
    the rest of the session."""
    def die():
        raise KeyboardInterrupt("interpreter going down")

    monkeypatch.setattr(chatfeed, "_build_badge_map", die)
    monkeypatch.setattr(chatfeed.threading, "Thread", _InlineThread)
    with pytest.raises(KeyboardInterrupt):
        build({**BASE, "badges": "broadcaster/1"}, "hi")
    assert chatfeed._badge_refreshing is False


def test_thread_start_failure_does_not_latch_the_flag(monkeypatch, badge_cache):
    class NoThreads:
        def __init__(self, **kw):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(chatfeed.threading, "Thread", NoThreads)
    build({**BASE, "badges": "broadcaster/1"}, "hi")     # must not raise
    assert chatfeed._badge_refreshing is False


# ----------------------------------------------------------------- /me -------
def test_action_message_is_unwrapped_and_flagged():
    m = build(BASE, "\x01ACTION waves at chat\x01")
    assert m["action"] is True
    assert m["text"] == "waves at chat"
    assert m["fragments"] == [{"type": "text", "text": "waves at chat"}]


def test_plain_message_is_not_an_action():
    assert build(BASE, "hello")["action"] is False


def test_action_emote_offsets_measured_against_the_wrapped_line():
    """The '\x01ACTION ' prefix is 8 code points, so a span counted against the
    wrapped line has to be shifted back by 8."""
    wrapped = "\x01ACTION Kappa hi\x01"
    assert wrapped[8:13] == "Kappa"            # 0=\x01, 1..6=ACTION, 7=space
    m = build({**BASE, "emotes": "25:8-12"}, wrapped)
    assert m["text"] == "Kappa hi"
    assert emotes(m)[0]["name"] == "Kappa"
    assert texts(m)[0]["text"] == " hi"


def test_action_emote_offsets_measured_against_the_body_also_work():
    """Twitch does not document which origin it uses for /me, so the feed
    detects it from the span instead of committing to one reading."""
    m = build({**BASE, "emotes": "25:0-4"}, "\x01ACTION Kappa hi\x01")
    assert emotes(m)[0]["name"] == "Kappa"
    assert texts(m)[0]["text"] == " hi"


def test_offset_detection_prefers_the_wrapped_reading_when_both_fit():
    """A body where BOTH origins land on a whole word. The wrapped reading is
    what other IRC clients compensate for, so it must win the tie."""
    body = "Kappa yo Kappa"            # 0-4 and 9-13 both are whole words
    assert chatfeed._spans_fit(body, [(0, 4, "25")], 0)
    assert chatfeed._spans_fit(body, [(9, 13, "25")], 0)
    assert chatfeed._best_offset(body, "25:8-12", 8) == 8


def test_offset_detection_rejects_a_span_landing_mid_word():
    """The defect the strict check exists for: shifting a body-relative span by
    8 lands inside the preceding word whenever that word is 7+ characters, and
    a mid-word slice has no whitespace in it, so the loose check accepted it and
    the emote image rendered over the middle of 'absolutely'."""
    body = "absolutely Kappa"
    assert body[3:8] == "olute"         # what offset 8 would have matched
    assert chatfeed._spans_fit(body, [(11, 15, "25")], 8, strict=False) is True
    assert chatfeed._spans_fit(body, [(11, 15, "25")], 8, strict=True) is False
    assert chatfeed._best_offset(body, "25:11-15", 8) == 0

    m = build({**BASE, "emotes": "25:11-15"}, "\x01ACTION absolutely Kappa\x01")
    assert emotes(m)[0]["name"] == "Kappa"
    assert texts(m)[0]["text"] == "absolutely "


def test_offset_detection_falls_back_to_loose_when_neither_origin_is_clean():
    """Strictness must never make this WORSE than trusting the wrapped reading:
    if no origin yields word-delimited spans, `shift` is kept."""
    body = "aaKappaaa"                  # not delimited at either origin
    assert chatfeed._spans_fit(body, [(10, 14, "25")], 8, strict=True) is False
    assert chatfeed._spans_fit(body, [(10, 14, "25")], 0, strict=True) is False
    assert chatfeed._best_offset(body, "25:10-14", 8) == 8


def test_emote_at_the_start_and_end_of_the_line_counts_as_delimited():
    assert chatfeed._spans_fit("Kappa", [(0, 4, "25")], 0) is True
    assert chatfeed._spans_fit("hi Kappa", [(3, 7, "25")], 0) is True


def test_unterminated_action_is_left_alone():
    m = build(BASE, "\x01ACTION no terminator")
    assert m["action"] is False


# ------------------------------------------------------- emote span bounds ---
def test_emote_span_running_past_the_end_is_dropped_not_clamped():
    """A clamped span leaves the cursor past the end and eats the rest of the
    line; dropping it keeps the text."""
    m = build({**BASE, "emotes": "25:0-99"}, "hello there")
    assert emotes(m) == []
    assert m["text"] == "hello there"
    assert texts(m)[0]["text"] == "hello there"


def test_emote_span_at_the_very_end_still_renders():
    m = build({**BASE, "emotes": "25:6-10"}, "hi... Kappa")
    assert emotes(m)[0]["name"] == "Kappa"


def test_emote_span_on_an_empty_message_is_dropped():
    m = build({**BASE, "emotes": "25:0-4"}, "")
    assert m["fragments"] == []


# --------------------------------------------------------------- moderation --
def test_clearmsg_targets_one_message():
    ev = chatfeed.clearmsg({"login": "BadActor", "target-msg-id": "abc-123"},
                           "the deleted text")
    assert ev["kind"] == "clearmsg"
    assert ev["v"] == chatfeed.CONTRACT_VERSION
    assert ev["target_id"] == "abc-123"
    assert ev["login"] == "badactor"
    assert ev["text"] == "the deleted text"


def test_clearchat_timeout_carries_user_and_duration():
    ev = chatfeed.clearchat({"target-user-id": "999", "ban-duration": "600"},
                            "BadActor")
    assert ev["kind"] == "clearchat"
    assert ev["user_id"] == "999"
    assert ev["login"] == "badactor"
    assert ev["seconds"] == 600


def test_clearchat_permanent_ban_has_no_duration():
    assert chatfeed.clearchat({"target-user-id": "999"}, "x")["seconds"] == 0


def test_clearchat_whole_room_has_no_target():
    ev = chatfeed.clearchat({}, "")
    assert ev["user_id"] == "" and ev["login"] == ""


@pytest.fixture
def emitted(monkeypatch):
    """Capture what chat.dispatch() puts on the overlay's bus."""
    out = []
    monkeypatch.setattr(chatmod.effects, "emit",
                        lambda ch, data, summary=None: out.append((ch, data)))
    yield out


def route(raw, emitted):
    """Feed one raw IRC line through the real dispatcher."""
    tags, prefix, cmd, params = chatmod._parse(raw)
    chatmod.dispatch(tags, prefix, cmd, params, "neothefox98")
    return emitted[-1][1] if emitted else None


def test_dispatch_routes_a_whole_room_clear(emitted):
    """One param = the whole room. Reading this as a per-user clear (or the
    reverse) is what wipes the overlay on every timeout, so this goes through
    chat.dispatch() rather than re-deriving its indexing."""
    ev = route(":tmi.twitch.tv CLEARCHAT #neothefox98", emitted)
    assert ev["kind"] == "clearchat"
    assert ev["user_id"] == "" and ev["login"] == ""


def test_dispatch_routes_a_per_user_timeout(emitted):
    ev = route("@ban-duration=60;target-user-id=42 "
               ":tmi.twitch.tv CLEARCHAT #neothefox98 :baddie", emitted)
    assert ev["kind"] == "clearchat"
    assert ev["login"] == "baddie" and ev["user_id"] == "42" and ev["seconds"] == 60


def test_dispatch_routes_clearmsg(emitted):
    ev = route("@login=baddie;target-msg-id=m-7 "
               ":tmi.twitch.tv CLEARMSG #neothefox98 :oops", emitted)
    assert ev["kind"] == "clearmsg"
    assert ev["target_id"] == "m-7" and ev["text"] == "oops"


def test_dispatch_clearmsg_without_a_body_does_not_report_the_channel_as_text(emitted):
    ev = route("@login=baddie;target-msg-id=m-8 :tmi.twitch.tv CLEARMSG #neothefox98",
               emitted)
    assert ev["text"] == ""


def test_dispatch_puts_messages_on_the_same_channel(emitted):
    route(line(BASE, "hello"), emitted)
    assert emitted[-1][0] == "chat"
    assert emitted[-1][1]["kind"] == "msg"


def test_dispatch_stops_on_failed_authentication():
    tags, prefix, cmd, params = chatmod._parse(
        ":tmi.twitch.tv NOTICE * :Login authentication failed")
    assert chatmod.dispatch(tags, prefix, cmd, params, "neothefox98") is False
    assert "authentication failed" in chatmod.status["error"]


def test_dispatch_survives_a_malformed_moderation_payload(emitted, monkeypatch):
    """A mod action is exactly when the IRC thread must not die."""
    monkeypatch.setattr(chatfeed, "clearchat",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    tags, prefix, cmd, params = chatmod._parse(":tmi.twitch.tv CLEARCHAT #neothefox98")
    assert chatmod.dispatch(tags, prefix, cmd, params, "neothefox98") is True


# ------------------------------------------------------- contract parity -----
def test_overlay_renders_the_contract_version_we_emit():
    """The renderer refuses any payload whose `v` it does not equal, so a bump
    on one side alone blanks the overlay on stream."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    js = os.path.join(here, "static", "chat", "prism-chat.js")
    if not os.path.isfile(js):
        pytest.skip("chat overlay not deployed into static/")
    src = open(js, encoding="utf-8").read()
    m = re.search(r"var\s+CONTRACT\s*=\s*(\d+)", src)
    assert m, "could not find CONTRACT in prism-chat.js"
    assert int(m.group(1)) == chatfeed.CONTRACT_VERSION


def test_unescape_tag():
    assert chatfeed.unescape_tag(r"a\sb") == "a b"
    assert chatfeed.unescape_tag(r"a\:b") == "a;b"
    assert chatfeed.unescape_tag("plain") == "plain"
    assert chatfeed.unescape_tag("") == ""
