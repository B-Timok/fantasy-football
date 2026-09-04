"""Minimal ANSI color helpers. Disabled automatically when stdout is not a
terminal, when NO_COLOR is set, or via --no-color."""
from __future__ import annotations

import os
import sys

ENABLED = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

_CODES = {
    "reset": "0", "bold": "1", "dim": "2",
    "red": "31", "green": "32", "yellow": "33", "blue": "34", "magenta": "35", "cyan": "36",
    "white": "37", "bright_red": "91", "bright_green": "92", "bright_yellow": "93",
    "bright_blue": "94", "bright_magenta": "95", "bright_cyan": "96",
}
POS_COLOR = {"QB": "bright_magenta", "RB": "bright_green", "WR": "bright_blue",
             "TE": "bright_yellow", "K": "white", "DEF": "white"}


def c(text, *styles: str) -> str:
    if not ENABLED or not styles:
        return str(text)
    codes = ";".join(_CODES[s] for s in styles)
    return f"\033[{codes}m{text}\033[0m"


def pos(text, position: str) -> str:
    return c(text, POS_COLOR.get(position, "white"))


def pct(p: float, text: str) -> str:
    """Green when likely available, yellow when a coin flip, red when likely gone."""
    if p >= 0.7:
        return c(text, "green")
    if p >= 0.35:
        return c(text, "yellow")
    return c(text, "red")


def reason(r: str) -> str:
    if r.startswith("MUST") or "injury risk" in r or "too early" in r:
        return c(r, "red")
    if "last " in r and "tier" in r:
        return c(r, "yellow")
    if r.startswith("you rank") or "likely still there" in r:
        return c(r, "green")
    if "waiting on" in r:
        return c(r, "cyan")
    return r


def pad(text: str, width: int, visible_len: int) -> str:
    """Right-pad a colored string to `width` using its visible length."""
    return text + " " * max(0, width - visible_len)
