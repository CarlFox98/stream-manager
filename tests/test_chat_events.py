"""Chat phases 5 and 6: inline sub/resub/gift/raid/cheer cards (USERNOTICE),
and 7TV / BTTV / FFZ emotes. No network: provider payloads are inline."""
import pytest

from stream_manager import chat as chatmod
from stream_manager import chatfeed, config as cfg


@pytest.fixture
def emitted(monkeypatch):
    out = []
    monkeypatch.setattr(chatmod.effects, "emit", lambda ch, data, summary=None: out.append((ch, data)))
    monkeypatch.setitem(cfg.config, "chat", {"event_cards": True, "third_party_emotes": True})
    yield out


def route(raw, emitted):
    tags, prefix, cmd, params = chatmod._parse(raw)
    chatmod.dispatch(tags, prefix, cmd, params, "neothefox98")
    return emitted[-1][1] if emitted else None


RESUB = ("@badges=subscriber/6;color=#FF0000;display-name=MothMage;emotes=;id=n-1;login=mothmage;"
         "msg-id=resub;msg-param-cumulative-months=6;msg-param-sub-plan=1000;"
         "system-msg=MothMage\\ssubscribed\\sat\\sTier\\s1.;tmi-sent-ts=1700000000000;user-id=77 "
         ":tmi.twitch.tv USERNOTICE #neothefox98 :six months already")


def test_resub_becomes_a_card_with_its_message(emitted):
    ev = route(RESUB, emitted)
    assert ev["kind"] == "event" and ev["v"] == chatfeed.CONTRACT_VERSION
    assert ev["event"]["type"] == "resub" and "6 months" in ev["event"]["label"]
    assert ev["event"]["system"] == "MothMage subscribed at Tier 1."
    assert ev["text"] == "six months already" and ev["user"]["name"] == "MothMage"


def test_raid_uses_the_raiders_name_and_count(emitted):
    ev = route("@display-name=StarRaider;login=starraider;msg-id=raid;msg-param-displayName=StarRaider;"
               "msg-param-viewerCount=24;user-id=9 :tmi.twitch.tv USERNOTICE #neothefox98", emitted)
    assert ev["event"]["type"] == "raid" and "24 viewers" in ev["event"]["label"]
    assert ev["text"] == "" and ev["fragments"] == []


def test_gifts_inside_a_gift_bomb_are_not_twenty_cards(emitted):
    """The submysterygift line already says 'gifting 20 subs' — one card per
    recipient on top of it would flush the whole chat off screen."""
    route("@display-name=Gen;login=gen;msg-id=submysterygift;msg-param-mass-gift-count=20;"
          "msg-param-sub-plan=1000;user-id=1 :tmi.twitch.tv USERNOTICE #neothefox98", emitted)
    n = len(emitted)
    route("@display-name=Gen;login=gen;msg-id=subgift;msg-param-community-gift-id=abc;"
          "msg-param-recipient-display-name=Lucky;user-id=1 :tmi.twitch.tv USERNOTICE #neothefox98", emitted)
    assert len(emitted) == n                                  # dropped, not emitted as None
    route("@display-name=Gen;login=gen;msg-id=subgift;msg-param-recipient-display-name=Lucky;"
          "msg-param-sub-plan=2000;user-id=1 :tmi.twitch.tv USERNOTICE #neothefox98", emitted)
    assert emitted[-1][1]["event"]["label"] == "gifted a Tier 2 sub to Lucky!"


def test_unshown_kinds_emit_nothing(emitted):
    route("@msg-id=bitsbadgetier;login=x;user-id=1 :tmi.twitch.tv USERNOTICE #neothefox98", emitted)
    assert emitted == []


def test_cards_can_be_turned_off(emitted, monkeypatch):
    monkeypatch.setitem(cfg.config, "chat", {"event_cards": False})
    route(RESUB, emitted)
    assert emitted == []


def test_a_cheer_stays_a_message_and_gains_a_card_line(emitted):
    ev = route("@bits=100;display-name=Delta;id=c1;user-id=5 :delta!delta@delta.tmi.twitch.tv "
               "PRIVMSG #neothefox98 :cheer100 for the fox", emitted)
    assert ev["kind"] == "msg" and ev["event"]["type"] == "cheer" and "100 bits" in ev["event"]["label"]


# ── third-party emotes ───────────────────────────────────────────────────────
MAP = {"catJAM": {"url": "https://cdn.7tv.app/emote/A/2x.webp", "provider": "7tv", "id": "A"},
       "monkaS": {"url": "https://cdn.betterttv.net/emote/B/2x", "provider": "bttv", "id": "B"}}


def test_whole_words_become_emotes_and_twitch_emotes_are_untouched():
    frags = [{"type": "text", "text": "catJAM so good catJAMs monkaS"},
             {"type": "emote", "id": "25", "name": "Kappa", "url": "u"},
             {"type": "mention", "text": "@catJAM", "login": "catjam", "self": False}]
    out = chatfeed.apply_third_party(frags, MAP)
    kinds = [(f["type"], f.get("name") or f.get("text")) for f in out]
    assert kinds == [("emote", "catJAM"), ("text", " so good catJAMs "), ("emote", "monkaS"),
                     ("emote", "Kappa"), ("mention", "@catJAM")]
    assert out[0]["provider"] == "7tv"


def test_channel_emotes_beat_global_ones(monkeypatch):
    def fake_get(url, timeout=6):
        if "7tv.io/v3/emote-sets/global" in url:
            return {"emotes": [{"name": "Clap", "id": "G7"}]}
        if "7tv.io/v3/users" in url:
            return {"emote_set": {"emotes": [{"name": "Clap", "id": "C7"}]}}
        raise OSError("down")                     # BTTV + FFZ unreachable: must not matter
    monkeypatch.setattr(chatfeed, "_get_json", fake_get)
    m = chatfeed._build_tp_map("150")
    assert m["Clap"]["id"] == "C7"


def test_all_providers_down_raises_so_the_retry_window_applies(monkeypatch):
    monkeypatch.setattr(chatfeed, "_get_json", lambda url, timeout=6: (_ for _ in ()).throw(OSError("x")))
    with pytest.raises(RuntimeError):
        chatfeed._build_tp_map("150")


def test_provider_parsers():
    assert chatfeed._bttv([{"code": "monkaS", "id": "B"}])["monkaS"]["url"].endswith("/B/2x")
    ffz = chatfeed._ffz({"sets": {"1": {"emoticons": [{"name": "LilZ", "id": 9, "urls": {"2": "//cdn.ffz/9/2"}}]}}})
    assert ffz["LilZ"]["url"] == "https://cdn.ffz/9/2"
    assert chatfeed._seventv({"emotes": [{"name": "x", "id": "1"}]})["x"]["provider"] == "7tv"
