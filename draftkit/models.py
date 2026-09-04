from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name: str) -> str:
    """Lower-case, strip accents/punctuation/suffixes so 'A.J. Brown Jr.' == 'aj brown'."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower().replace("'", "").replace(".", "")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    parts = [p for p in s.split() if p and p not in _SUFFIXES]
    return " ".join(parts)


def normalize_pos(pos: str) -> Optional[str]:
    p = re.sub(r"[^A-Z/]", "", pos.upper())
    if p in ("DST", "D/ST", "DEF", "D", "DEFENSE"):
        return "DEF"
    if p in ("PK", "K"):
        return "K"
    if p in POSITIONS:
        return p
    return None


@dataclass
class Player:
    name: str
    pos: str
    rank: int
    team: str = ""
    pos_rank: int = 0
    tier: Optional[int] = None
    adp: Optional[float] = None
    bye: Optional[int] = None
    key: str = field(default="", repr=False)

    def __post_init__(self):
        if not self.key:
            self.key = normalize_name(self.name)

    @property
    def adp_or_rank(self) -> float:
        return float(self.adp) if self.adp is not None else float(self.rank)

    def label(self) -> str:
        t = f" {self.team}" if self.team else ""
        return f"{self.name} ({self.pos}{self.pos_rank}{t})"


@dataclass
class League:
    teams: int = 12
    rounds: int = 15
    scoring: str = "ppr"
    snake: bool = True
    roster: dict = field(default_factory=lambda: {
        "QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1, "BN": 6})
    flex_positions: tuple = ("RB", "WR", "TE")
    sleeper_draft_id: str = ""
    sleeper_username: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "League":
        lg = cls()
        for k, v in d.items():
            if hasattr(lg, k):
                setattr(lg, k, tuple(v) if k == "flex_positions" else v)
        if "rounds" not in d:
            lg.rounds = sum(lg.roster.values())
        return lg

    # --- pick arithmetic -------------------------------------------------
    def round_of(self, pick_no: int) -> int:
        return (pick_no - 1) // self.teams + 1

    def slot_of(self, pick_no: int) -> int:
        r = self.round_of(pick_no)
        i = (pick_no - 1) % self.teams + 1
        if self.snake and r % 2 == 0:
            return self.teams + 1 - i
        return i

    def picks_for_slot(self, slot: int) -> list[int]:
        out = []
        for r in range(1, self.rounds + 1):
            if self.snake and r % 2 == 0:
                i = self.teams + 1 - slot
            else:
                i = slot
            out.append((r - 1) * self.teams + i)
        return out

    @property
    def total_picks(self) -> int:
        return self.teams * self.rounds
