"""Chat-command registry: enable/disable built-ins and manage custom commands.

The built-in game/quote commands live in games.py; this module is the source of
truth for *which* of them are active and adds user-defined **custom commands**
(simple `!name -> response` replies). Both are driven from `config.json` →
`commands` so they can be edited from the dashboard and hot-reloaded.

Config shape (all optional):

    "commands": {
      "coinflip": { "enabled": false },        # toggle a built-in off
      "custom": {
        "socials":  { "response": "Follow me everywhere: ...", "permission": "everyone", "enabled": true },
        "discord":  { "response": "Join: discord.gg/...", "enabled": true }
      }
    }
"""
import re

from .config import config

# Canonical built-ins: name -> (aliases, description, category, mods_only)
BUILTINS = [
    ("coinflip",   ["coinflip", "flip", "coin"],        "Flip a coin (Heads/Tails)",     "Games",  False),
    ("5050",       ["5050", "fiftyfifty", "50/50"],     "50/50 gamble",                  "Games",  False),
    ("slots",      ["slots", "slot"],                   "Spin the slot machine",         "Games",  False),
    ("dice",       ["dice", "roll"],                    "Roll a die (!dice [sides])",    "Games",  False),
    ("8ball",      ["8ball", "eightball"],              "Ask the Magic 8-Ball",          "Games",  False),
    ("duel",       ["duel", "fight"],                   "Duel another viewer",           "Games",  False),
    ("lucky",      ["lucky", "luckywheel", "luckyspin"],"Spin the Lucky Wheel",          "Wheels", True),
    ("risky",      ["risky", "riskywheel", "riskyspin"],"Spin the Risky Wheel",          "Wheels", True),
    ("quote",      ["quote"],                           "Show/add/delete quotes",        "Quotes", False),
    ("addquote",   ["addquote"],                        "Add a quote (mods)",            "Quotes", True),
    ("delquote",   ["delquote"],                        "Delete a quote (mods)",         "Quotes", True),
    ("quotecount", ["quotecount"],                      "How many quotes there are",     "Quotes", False),
    ("song",       ["song", "nowplaying", "np"],        "Show the current Spotify track", "Music",  False),
    ("commands",   ["commands", "help"],                "List available commands",       "Meta",   False),
]

# alias -> canonical name
_ALIAS = {}
for _name, _aliases, _desc, _cat, _mods in BUILTINS:
    for _a in _aliases:
        _ALIAS[_a] = _name


def canonical_for(cmd):
    """Return the canonical built-in name for a typed command/alias, or None."""
    return _ALIAS.get((cmd or "").lower())


def _cmd_cfg():
    c = config.get("commands")
    return c if isinstance(c, dict) else {}


def is_enabled(canonical):
    """Whether a built-in command is currently active (default: True)."""
    entry = _cmd_cfg().get(canonical)
    if isinstance(entry, dict) and "enabled" in entry:
        return bool(entry["enabled"])
    return True


def set_enabled(canonical, enabled):
    """Persist a built-in's enabled state. Returns True if it's a known built-in."""
    if canonical not in _ALIAS.values():
        return False
    from . import config as config_mod
    config_mod.save_config({"commands": {canonical: {"enabled": bool(enabled)}}})
    return True


# ── custom commands ────────────────────────────────────────────────────────
def _customs():
    c = _cmd_cfg().get("custom")
    return c if isinstance(c, dict) else {}


def get_custom(name):
    """Return a custom command dict for `name` (lowercased), or None."""
    return _customs().get((name or "").lower().lstrip("!"))


def list_customs():
    out = []
    for name, c in _customs().items():
        if not isinstance(c, dict):
            continue
        out.append({
            "name": name,
            "response": c.get("response", ""),
            "permission": c.get("permission", "everyone"),
            "enabled": bool(c.get("enabled", True)),
        })
    out.sort(key=lambda x: x["name"])
    return out


_NAME_RE = re.compile(r"^[a-z0-9_]{1,25}$")


def upsert_custom(name, response, permission="everyone", enabled=True):
    """Add or update a custom command. Returns (ok, message)."""
    name = (name or "").lower().lstrip("!").strip()
    if not _NAME_RE.match(name):
        return False, "Name must be 1-25 chars: letters, numbers, underscore"
    if canonical_for(name):
        return False, f"'{name}' is a built-in command — toggle it in the built-ins list instead"
    response = (response or "").strip()
    if not response:
        return False, "Response text is required"
    if permission not in ("everyone", "mods"):
        permission = "everyone"
    from . import config as config_mod
    config_mod.save_config({"commands": {"custom": {name: {
        "response": response[:400], "permission": permission, "enabled": bool(enabled),
    }}}})
    return True, f"Saved !{name}"


def delete_custom(name):
    """Remove a custom command. Returns True if it existed."""
    name = (name or "").lower().lstrip("!").strip()
    if name not in _customs():
        return False
    from . import config as config_mod
    config_mod.delete_config_key(["commands", "custom", name])
    return True


def render(response, user, args):
    """Fill a custom command's response template with simple variables.

    {user} = caller's name, {args} = everything after the command,
    {1}, {2}, ... = individual whitespace-separated arguments (blank if absent).
    """
    text = response or ""
    text = text.replace("{user}", user or "")
    text = text.replace("{args}", " ".join(args))
    def _num(m):
        i = int(m.group(1))
        return args[i - 1] if 1 <= i <= len(args) else ""
    text = re.sub(r"\{(\d+)\}", _num, text)
    return text.replace("\r", " ").replace("\n", " ")[:480]


def permission_ok(custom, can_edit):
    """Whether the caller may run this custom command."""
    return can_edit or custom.get("permission", "everyone") != "mods"


# ── dashboard view ──────────────────────────────────────────────────────────
def public_list():
    """Everything the Commands tab needs."""
    prefix = config.get("command_prefix", "!") or "!"
    builtins = []
    for name, aliases, desc, cat, mods_only in BUILTINS:
        builtins.append({
            "name": name, "aliases": aliases, "description": desc,
            "category": cat, "mods_only": mods_only, "enabled": is_enabled(name),
        })
    return {"prefix": prefix, "builtins": builtins, "custom": list_customs()}
