"""Terminal ANSI styling helpers."""
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

S = {
    "R": "\033[0m", "B": "\033[1m", "D": "\033[90m",
    "G": "\033[92m", "Y": "\033[93m", "C": "\033[96m",
    "M": "\033[95m", "W": "\033[97m",
}


def style(tag, text=""):
    return f"{S.get(tag, '')}{text}{S['R']}"


def icon(ok):
    return f"{style('G', '●')}" if ok else f"{style('R', '○')}"
