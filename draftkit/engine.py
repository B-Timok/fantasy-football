"""Scoring: which available player should you take right now?

score = value * strategy_mult * need_mult
        + URGENCY_W * max(0, value - E[best value at this position at your next pick])
        + tier bonuses

The "urgency" term is the core idea: how much worse will your best option at
this position be if you wait until your next pick?  It's computed from ADP
(or your rank if no ADP) and how many picks happen before you're up again.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from .models import League, Player, POSITIONS
from .strategies import Strategy

URGENCY_W = 0.8          # weight of drop-off-if-you-wait
LAST_IN_TIER_BONUS = 4.0  # last player in a tier at his position
BENCH_DISCOUNT = {"QB": 0.45, "RB": 0.8, "WR": 0.8, "TE": 0.45, "K": 0.05, "DEF": 0.05}
FLEX_DISCOUNT = 0.92


def base_value(rank: int, horizon: int = 220) -> float:
    """Map overall rank to a 0-100 value with a steep top end."""
    x = min(max(rank - 1, 0), horizon) / horizon
    return 100.0 * (1.0 - x) ** 1.6


def player_value(p: Player) -> float:
    """K/DEF are ranked ~150+ overall, which would make them worthless; give
    them a modest positional floor so they can win the last rounds."""
    v = base_value(p.rank)
    if p.pos in ("K", "DEF"):
        v = max(v, 25.0 - 1.0 * p.pos_rank)
    return v


def p_available(player: Player, at_pick: int, now_pick: int) -> float:
    """P(still on the board when pick `at_pick` comes up | on the board at `now_pick`)."""
    adp = player.adp_or_rank
    # logistic scale; its SD is ~1.8x the scale, so ADP 20 -> SD ~4 picks, ADP 100 -> SD ~14
    spread = 1.0 + 0.07 * adp

    def surv(pick: float) -> float:
        """P(not taken by any of picks 1..pick-1), i.e. still there when `pick` is on the clock."""
        z = (pick - 0.5 - adp) / spread
        z = max(-40.0, min(40.0, z))
        return 1.0 / (1.0 + math.exp(z))

    s_now = surv(now_pick)
    s_then = surv(at_pick)
    if s_now <= 1e-9:
        return 1.0 if at_pick <= now_pick else 0.05
    return max(0.0, min(1.0, s_then / s_now))


@dataclass
class RosterView:
    starters: dict = field(default_factory=dict)   # slot name -> list[Player]
    bench: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)      # pos -> n drafted

    def open_starters(self, league: League) -> dict:
        out = {}
        for slot, n in league.roster.items():
            if slot == "BN":
                continue
            out[slot] = n - len(self.starters.get(slot, []))
        return out


def build_roster(my_players: list[Player], league: League) -> RosterView:
    rv = RosterView(starters={s: [] for s in league.roster if s != "BN"})
    for p in sorted(my_players, key=lambda p: p.rank):
        rv.counts[p.pos] = rv.counts.get(p.pos, 0) + 1
        if len(rv.starters.get(p.pos, [])) < league.roster.get(p.pos, 0):
            rv.starters[p.pos].append(p)
        elif (p.pos in league.flex_positions and
              len(rv.starters.get("FLEX", [])) < league.roster.get("FLEX", 0)):
            rv.starters["FLEX"].append(p)
        else:
            rv.bench.append(p)
    return rv


def need_multiplier(pos: str, roster: RosterView, league: League, rnd: int) -> tuple[float, str]:
    opened = roster.open_starters(league)
    if opened.get(pos, 0) > 0:
        return 1.0, f"{pos} starter open"
    if pos in league.flex_positions and opened.get("FLEX", 0) > 0:
        return FLEX_DISCOUNT, "FLEX open"
    return BENCH_DISCOUNT[pos], "bench"


def must_fill_multiplier(pos: str, roster: RosterView, league: League,
                         picks_remaining: int) -> float:
    """When you have as many picks left as open starting slots, only players
    who fill a starting slot get full value."""
    opened = roster.open_starters(league)
    n_open = sum(opened.values())
    if n_open == 0 or picks_remaining > n_open:
        return 1.0
    fills = opened.get(pos, 0) > 0 or (pos in league.flex_positions and opened.get("FLEX", 0) > 0)
    return 1.0 if fills else 0.15


def kicker_def_gate(pos: str, rnd: int, league: League) -> float:
    """Nobody should take K/DEF early no matter what the rankings say."""
    if pos not in ("K", "DEF"):
        return 1.0
    left = league.rounds - rnd  # rounds remaining after this one
    if left >= 3:
        return 0.05
    if left == 2:
        return 0.6
    return 1.0


@dataclass
class PosOutlook:
    pos: str
    best_now: Optional[Player]
    exp_value_next: float
    likely_next: Optional[Player]   # most probable "best available" at your next pick
    n_expected_gone: float          # expected number at this pos taken before your next pick
    dropoff: float


def position_outlook(available: list[Player], pos: str, now_pick: int,
                     next_pick: Optional[int]) -> PosOutlook:
    pool = [p for p in available if p.pos == pos]
    pool.sort(key=lambda p: p.rank)
    if not pool:
        return PosOutlook(pos, None, 0.0, None, 0.0, 0.0)
    best = pool[0]
    if next_pick is None:
        v = player_value(best)
        return PosOutlook(pos, best, v, best, 0.0, 0.0)
    exp_v = 0.0
    p_none_before = 1.0
    likely, likely_p = best, 0.0
    n_gone = 0.0
    for pl in pool[:40]:
        pa = p_available(pl, next_pick, now_pick)
        n_gone += 1.0 - pa
        p_first = p_none_before * pa
        exp_v += p_first * player_value(pl)
        if p_first > likely_p:
            likely, likely_p = pl, p_first
        p_none_before *= (1.0 - pa)
        if p_none_before < 1e-4:
            break
    dropoff = max(0.0, player_value(best) - exp_v)
    return PosOutlook(pos, best, exp_v, likely, n_gone, dropoff)


@dataclass
class Recommendation:
    player: Player
    score: float
    value: float
    p_avail_next: float
    urgency: float
    strat_mult: float
    need_mult: float
    reasons: list


def _last_in_tier(player: Player, available: list[Player]) -> bool:
    if player.tier is None:
        return False
    same = [p for p in available if p.pos == player.pos and p.tier == player.tier]
    return len(same) == 1


def recommend(available: list[Player], my_players: list[Player], league: League,
              strategy: Strategy, now_pick: int, next_pick: Optional[int],
              top_n: int = 10) -> tuple[list[Recommendation], dict]:
    rnd = league.round_of(now_pick)
    roster = build_roster(my_players, league)
    my_slot = league.slot_of(now_pick)
    picks_remaining = sum(1 for pk in league.picks_for_slot(my_slot) if pk >= now_pick)
    outlooks = {pos: position_outlook(available, pos, now_pick, next_pick) for pos in POSITIONS}
    recs = []
    for p in available:
        value = player_value(p)
        sm = strategy.weight(p.pos, rnd, roster.counts) * kicker_def_gate(p.pos, rnd, league)
        nm, need_reason = need_multiplier(p.pos, roster, league, rnd)
        mf = must_fill_multiplier(p.pos, roster, league, picks_remaining)
        nm *= mf
        pa = p_available(p, next_pick, now_pick) if next_pick else 1.0
        urgency = max(0.0, value - outlooks[p.pos].exp_value_next) if next_pick else 0.0
        score = value * sm * nm + URGENCY_W * urgency * sm * nm
        reasons = []
        if mf < 1.0:
            reasons.append("doesn't fill a starter (few picks left)")
        elif picks_remaining <= sum(roster.open_starters(league).values()) and need_reason != "bench":
            reasons.append("MUST fill: " + need_reason)
            need_reason = "bench"
        if need_reason != "bench":
            reasons.append(need_reason)
        else:
            reasons.append(f"{p.pos} depth")
        if next_pick:
            if pa < 0.35:
                reasons.append(f"{100 - pa * 100:.0f}% gone by your next pick")
            elif pa > 0.8:
                reasons.append(f"likely still there ({pa * 100:.0f}%)")
        if urgency >= 6:
            reasons.append(f"{p.pos} drops {urgency:.0f} if you wait")
        if _last_in_tier(p, available):
            score += LAST_IN_TIER_BONUS * nm
            reasons.append(f"last {p.pos} in tier {p.tier}")
        if sm > 1.02:
            reasons.append(f"{strategy.name} +{(sm - 1) * 100:.0f}%")
        elif sm < 0.98:
            reasons.append(f"{strategy.name} -{(1 - sm) * 100:.0f}%")
        if p.adp is not None and p.adp - now_pick >= 10:
            reasons.append(f"value (ADP {p.adp:.0f})")
        recs.append(Recommendation(p, score, value, pa, urgency, sm, nm, reasons))
    recs.sort(key=lambda r: -r.score)
    return recs[:top_n], outlooks
