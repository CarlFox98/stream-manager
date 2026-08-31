"""Terminal ANSI styling helpers — PRISM palette.

Colors match the PRISM overlay theme (prism-theme.css): teal #57F2E4,
blue #6C8BFF, purple #B983FF, pink #FF7ACB, gold #FFD86B on near-black.
The public API (``S``, ``style``, ``icon``) is unchanged; only the color
values were upgraded from 16-color to 24-bit truecolor, so existing call
sites recolor automatically.
"""
import os, sys


def _init_ansi():
    if os.name == "nt":
        os.system("")                              # enable VT processing
        os.system("chcp 65001 >nul 2>&1")          # UTF-8 codepage
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass  # Python < 3.7


_init_ansi()


def _fg(r, g, b):
    return f"\033[38;2;{r};{g};{b}m"


# PRISM palette (24-bit). Keys preserved for backward compatibility:
#   B bold · D dim · G success · Y warning · C info · M structure · W emphasis
S = {
    "R": "\033[0m",
    "B": "\033[1m",
    "D": _fg(136, 146, 176),   # muted slate
    "G": _fg(87, 242, 228),    # teal  (success / ✓ / live)
    "Y": _fg(255, 216, 107),   # gold  (warning)
    "C": _fg(108, 139, 255),   # blue  (info / keys)
    "M": _fg(185, 131, 255),   # purple (structure / borders)
    "W": _fg(234, 240, 255),   # ink   (emphasis)
}

# Named accents for a PRISM gradient wordmark.
_TEAL, _BLUE, _PURPLE, _PINK, _GOLD = (
    _fg(87, 242, 228), _fg(108, 139, 255), _fg(185, 131, 255),
    _fg(255, 122, 203), _fg(255, 216, 107),
)
_RAMP = [_PURPLE, _BLUE, _TEAL, _PINK, _GOLD]
_RED = _fg(240, 120, 120)


def style(tag, text=""):
    return f"{S.get(tag, '')}{text}{S['R']}"


def grad(text, bold=True):
    """Color each letter across the PRISM ramp — the iridescent wordmark look."""
    b = S["B"] if bold else ""
    out, i = [], 0
    for ch in text:
        if ch == " ":
            out.append(" ")
            continue
        out.append(f"{b}{_RAMP[i % len(_RAMP)]}{ch}")
        i += 1
    return "".join(out) + S["R"]


def icon(ok):
    return f"{S['G']}●{S['R']}" if ok else f"{_RED}○{S['R']}"
