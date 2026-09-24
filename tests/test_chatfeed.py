"""Contract tests for the PRISM chat feed (stream_manager/chatfeed.py).

Recorded IRC lines in, message objects out. No network, no live Twitch: the
badge map is stubbed, so every test here is pure logic.

Run from the repo root:   python -m pytest -q
"""
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


def test_badge_fetch_failure_still_renders_the_message(monkeypatch):
    """A dead Helix call must cost badges, never the message."""
    monkeypatch.setattr(chatfeed, "_badge_cache",
                        {"map": {}, "at": 0.0, "ok": False})
    monkeypatch.setattr(chatfeed, "_badge_map", _REAL_BADGE_MAP)   # un-stub

    def boom(*a, **k):
        raise OSError("helix down")
    monkeypatch.setattr(chatfeed, "_fetch_badge_sets", boom)

    tags = {**BASE, "badges": "broadcaster/1"}
    m = build(tags, "still here")
    assert m["text"] == "still here"
    assert m["badges"] == [{"set": "broadcaster", "version": "1",
                            "url": "", "title": "broadcaster"}]


def test_unescape_tag():
    assert chatfeed.unescape_tag(r"a\sb") == "a b"
    assert chatfeed.unescape_tag(r"a\:b") == "a;b"
    assert chatfeed.unescape_tag("plain") == "plain"
    assert chatfeed.unescape_tag("") == ""
