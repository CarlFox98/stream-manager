"""Twitch chat over raw IRC (TLS) — no third-party websocket dependency.

Twitch exposes plain IRC on irc.chat.twitch.tv:6697 (TLS), which the standard
library's socket + ssl handle directly. We authenticate with the broadcaster's
user token (from twitch_auth), request message tags so we can tell mods from
regular viewers, and hand every "!command" line to games.handle_command.

Runs on a daemon thread with automatic reconnect + exponential backoff.
"""
import socket, ssl, threading, time

from . import games, twitch_auth
from .config import TWITCH_USER, config

HOST = "irc.chat.twitch.tv"
PORT = 6697

status = {"connected": False, "channel": "", "error": "", "sent": 0, "received": 0}

_stop = threading.Event()
_send_lock = threading.Lock()
_sock = None
_last_send = 0.0
# Twitch chat rate limits: broadcaster/mod = 100 msgs/30s, everyone else = 20/30s.
# We space sends to stay comfortably under whichever applies to the bot account.
_min_interval = 1.6   # safe default until we know the bot is broadcaster/mod


def _parse(line):
    """Parse one IRC line into (tags: dict, prefix, command, params: list)."""
    tags = {}
    rest = line
    if rest.startswith("@"):
        tagpart, rest = rest[1:].split(" ", 1)
        for kv in tagpart.split(";"):
            k, _, v = kv.partition("=")
            tags[k] = v
    prefix = ""
    if rest.startswith(":"):
        prefix, rest = rest[1:].split(" ", 1)
    if " :" in rest:
        head, trailing = rest.split(" :", 1)
        params = head.split()
        params.append(trailing)
    else:
        params = rest.split()
    command = params[0].upper() if params else ""
    return tags, prefix, command, params[1:]


def _is_mod(tags):
    if tags.get("mod") == "1":
        return True
    badges = tags.get("badges", "")
    return any(b.split("/")[0] in ("moderator", "broadcaster") for b in badges.split(","))


def _is_broadcaster(tags, nick, channel):
    badges = tags.get("badges", "")
    if any(b.startswith("broadcaster/") for b in badges.split(",")):
        return True
    return nick.lower() == channel.lower()


def _raw(line):
    global _sock
    if _sock:
        try:
            _sock.sendall((line + "\r\n").encode("utf-8"))
        except OSError as e:
            status["error"] = f"send failed: {e}"


def say(text):
    """Send a chat message to the joined channel (lightly rate-limited)."""
    global _last_send
    ch = status["channel"]
    if not ch or not text:
        return
    with _send_lock:
        gap = time.time() - _last_send
        if gap < _min_interval:            # stay well under Twitch's rate limits
            time.sleep(_min_interval - gap)
        # IRC lines can't contain newlines; clamp length defensively.
        clean = text.replace("\r", " ").replace("\n", " ")[:480]
        _raw(f"PRIVMSG #{ch} :{clean}")
        _last_send = time.time()
        status["sent"] += 1


def _handle_privmsg(tags, prefix, params, channel):
    nick = prefix.split("!", 1)[0]
    user = tags.get("display-name") or nick
    text = params[-1] if params else ""
    status["received"] += 1
    prefix_char = config.get("command_prefix", "!") or "!"
    if not text.startswith(prefix_char):
        return
    try:
        games.handle_command(
            text, user,
            is_mod=_is_mod(tags),
            is_broadcaster=_is_broadcaster(tags, nick, channel),
            say=say,
            prefix=prefix_char,
            user_id=tags.get("user-id", ""),
        )
    except Exception as e:
        print(f"[chat] command error: {e}")


def _connect_and_run():
    global _sock
    token = twitch_auth.get_user_token()
    login = twitch_auth.auth.get("login")
    channel = (TWITCH_USER or login or "").lower()
    if not token or not login or not channel:
        status["error"] = "not authorized"
        return False

    ctx = ssl.create_default_context()
    raw = socket.create_connection((HOST, PORT), timeout=15)
    _sock = ctx.wrap_socket(raw, server_hostname=HOST)
    _sock.settimeout(1.0)

    _raw("CAP REQ :twitch.tv/tags twitch.tv/commands")
    _raw(f"PASS oauth:{token}")
    _raw(f"NICK {login}")
    _raw(f"JOIN #{channel}")
    status["channel"] = channel
    status["error"] = ""
    # If the bot IS the broadcaster (the usual setup), it gets the 100/30s limit,
    # so we can send faster. A separate, non-mod bot account stays at the safe pace.
    global _min_interval
    _min_interval = 0.5 if login.lower() == channel.lower() else 1.6

    buf = ""
    while not _stop.is_set():
        try:
            chunk = _sock.recv(4096)
        except socket.timeout:
            continue
        except OSError as e:
            status["error"] = f"recv failed: {e}"
            break
        if not chunk:
            break  # server closed the connection
        buf += chunk.decode("utf-8", "replace")
        while "\r\n" in buf:
            line, buf = buf.split("\r\n", 1)
            if not line:
                continue
            tags, prefix, command, params = _parse(line)
            if command == "PING":
                _raw("PONG :" + (params[-1] if params else "tmi.twitch.tv"))
            elif command == "PRIVMSG":
                _handle_privmsg(tags, prefix, params, channel)
            elif command in ("001", "GLOBALUSERSTATE", "JOIN"):
                status["connected"] = True
            elif command == "NOTICE" and params and "authentication failed" in params[-1].lower():
                status["error"] = "authentication failed — re-authorize on the dashboard"
                _stop_flag_soft()
                return False
    return True


def _stop_flag_soft():
    status["connected"] = False


def _run_forever():
    backoff = 2
    while not _stop.is_set():
        status["connected"] = False
        try:
            ok = _connect_and_run()
        except Exception as e:
            status["error"] = str(e)
            ok = False
        finally:
            _close()
        if status["error"] == "authentication failed — re-authorize on the dashboard":
            return
        if _stop.is_set():
            return
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)   # exponential backoff, capped


def _close():
    global _sock
    status["connected"] = False
    if _sock:
        try:
            _sock.close()
        except OSError:
            pass
        _sock = None


def start():
    """Launch the chat client on a daemon thread. Idempotent."""
    if getattr(start, "_thread", None) and start._thread.is_alive():
        return
    _stop.clear()
    start._thread = threading.Thread(target=_run_forever, daemon=True)
    start._thread.start()


def stop():
    _stop.set()
    _close()
