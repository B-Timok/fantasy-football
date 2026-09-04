"""Draft strategies, expressed as position multipliers that depend on the round
and on what you already have. A multiplier > 1 pushes a position up the board,
< 1 pushes it down. Edit or add entries freely."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# counts: {"QB": n, "RB": n, ...} of players you have already drafted
Weight = Callable[[str, int, dict], float]


@dataclass
class Strategy:
    name: str
    description: str
    weight: Weight
    suggested_slots: str = ""


def _bpa(pos, rnd, counts):
    return 1.0


def _zero_rb(pos, rnd, counts):
    if rnd <= 5:
        return {"RB": 0.55, "WR": 1.15, "TE": 1.05, "QB": 1.0}.get(pos, 1.0)
    if rnd <= 10:
        return {"RB": 1.25, "WR": 0.95}.get(pos, 1.0)
    return 1.0


def _hero_rb(pos, rnd, counts):
    have_rb = counts.get("RB", 0)
    if have_rb == 0 and rnd <= 2:
        return {"RB": 1.2, "WR": 0.95}.get(pos, 1.0)
    if have_rb >= 1 and rnd <= 6:
        return {"RB": 0.6, "WR": 1.15, "TE": 1.05}.get(pos, 1.0)
    if rnd <= 10:
        return {"RB": 1.2}.get(pos, 1.0)
    return 1.0


def _robust_rb(pos, rnd, counts):
    if rnd <= 3 and counts.get("RB", 0) < 2:
        return {"RB": 1.2, "WR": 0.95, "TE": 0.9, "QB": 0.85}.get(pos, 1.0)
    if rnd <= 5 and counts.get("RB", 0) < 3:
        return {"RB": 1.08}.get(pos, 1.0)
    return 1.0


def _balanced(pos, rnd, counts):
    # mild: don't take a 3rd RB/WR before having 2 of the other in the first 5 rounds
    if rnd <= 5:
        rb, wr = counts.get("RB", 0), counts.get("WR", 0)
        if pos == "RB" and rb >= 2 and wr < 2:
            return 0.85
        if pos == "WR" and wr >= 2 and rb < 2:
            return 0.85
        if pos == "QB" and rnd <= 3:
            return 0.9
    return 1.0


def _elite_onesies(pos, rnd, counts):
    # go get a top QB and top TE early, then hammer RB/WR
    if rnd <= 4:
        if pos == "QB" and counts.get("QB", 0) == 0:
            return 1.15
        if pos == "TE" and counts.get("TE", 0) == 0:
            return 1.15
    return 1.0


STRATEGIES: dict[str, Strategy] = {
    "bpa": Strategy("bpa", "Best player available. Trust your rankings; need only nudges.", _bpa,
                    "any slot"),
    "balanced": Strategy("balanced", "BPA with a nudge to have 2 RB + 2 WR by round 5.",
                         _balanced, "any slot; good default for mid picks 5-8"),
    "hero_rb": Strategy("hero_rb", "One elite RB in rounds 1-2, then WR/TE heavy until round 6, "
                        "then RB depth.", _hero_rb, "early picks 1-4"),
    "zero_rb": Strategy("zero_rb", "No RB in rounds 1-5, load WR/TE, then buy RB volume "
                        "rounds 6-10.", _zero_rb, "late picks 9-12 or when the RB tier is gone"),
    "robust_rb": Strategy("robust_rb", "Two RBs in the first three rounds, a third by round 5.",
                          _robust_rb, "picks 1-6 when RBs fall to you"),
    "elite_onesies": Strategy("elite_onesies", "Lock an elite QB and TE early, then RB/WR.",
                              _elite_onesies, "mid/late picks in a league that waits on QB/TE"),
}


def get(name: str) -> Strategy:
    key = name.lower().replace("-", "_").replace(" ", "_")
    if key not in STRATEGIES:
        raise KeyError(f"unknown strategy '{name}'. Options: {', '.join(STRATEGIES)}")
    return STRATEGIES[key]


def suggest_for_slot(slot: int, teams: int) -> str:
    third = max(1, teams // 3)
    if slot <= third:
        return "hero_rb"
    if slot <= 2 * third:
        return "balanced"
    return "zero_rb"
