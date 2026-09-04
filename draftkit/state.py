"""Draft state: the list of picks so far, your slot, current strategy. Saved as
JSON after every change so a crash or a closed terminal loses nothing."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Optional

from .models import League, Player


@dataclass
class Pick:
    pick_no: int
    key: str
    name: str
    pos: str
    slot: int
    mine: bool


@dataclass
class DraftState:
    slot: int
    strategy: str
    picks: list = field(default_factory=list)   # list[Pick], in pick order
    path: str = field(default="", repr=False)

    # --- persistence ---------------------------------------------------
    def save(self) -> None:
        if not self.path:
            return
        d = {"slot": self.slot, "strategy": self.strategy,
             "picks": [asdict(p) for p in self.picks]}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, self.path)

    @classmethod
    def load(cls, path: str) -> Optional["DraftState"]:
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        st = cls(slot=d["slot"], strategy=d["strategy"], path=path)
        st.picks = [Pick(**p) for p in d.get("picks", [])]
        return st

    # --- queries ---------------------------------------------------------
    @property
    def current_pick(self) -> int:
        return len(self.picks) + 1

    def taken_keys(self) -> set[str]:
        return {p.key for p in self.picks}

    def my_keys(self) -> list[str]:
        return [p.key for p in self.picks if p.mine]

    def next_my_pick(self, league: League, after: Optional[int] = None) -> Optional[int]:
        """First of my picks strictly after `after` (default: after the current pick)."""
        after = self.current_pick if after is None else after
        for pk in league.picks_for_slot(self.slot):
            if pk > after:
                return pk
        return None

    def is_my_pick(self, league: League, pick_no: Optional[int] = None) -> bool:
        pick_no = pick_no or self.current_pick
        return league.slot_of(pick_no) == self.slot

    # --- mutation --------------------------------------------------------
    def add_pick(self, player: Player, league: League, mine: Optional[bool] = None) -> Pick:
        pick_no = self.current_pick
        slot = league.slot_of(pick_no)
        if mine is None:
            mine = slot == self.slot
        pk = Pick(pick_no=pick_no, key=player.key, name=player.name, pos=player.pos,
                  slot=slot, mine=mine)
        self.picks.append(pk)
        self.save()
        return pk

    def undo(self) -> Optional[Pick]:
        if not self.picks:
            return None
        pk = self.picks.pop()
        self.save()
        return pk
