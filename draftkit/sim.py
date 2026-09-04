"""Draft simulation: ADP-driven opponents, a roster scorer, and batch runs.

Used by --mock in the CLI (single opponent picks) and by simulate.py (many
full drafts to compare strategies per slot and tune engine weights).
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from . import engine
from .models import League, Player, POSITIONS
from .strategies import Strategy

MOCK_CAPS = {"QB": 2, "RB": 7, "WR": 7, "TE": 2, "K": 1, "DEF": 1}
POS_DEFAULT_PPG = {"QB": 14.0, "RB": 8.0, "WR": 8.0, "TE": 6.0, "K": 8.0, "DEF": 6.0}


def opponent_pick(available: list[Player], counts: dict, rnd: int, league: League,
                  rng: random.Random, noise: float = 1.0) -> Optional[Player]:
    """Pick for a simulated opponent: near the top of the ADP board, with
    noise that grows later in the draft; simple roster caps; K/DEF late."""
    if not available:
        return None
    rounds_left = league.rounds - rnd
    need_kd = [p for p in ("K", "DEF") if counts.get(p, 0) == 0]

    def ok(p: Player, strict: bool) -> bool:
        if counts.get(p.pos, 0) >= MOCK_CAPS.get(p.pos, 9):
            return False
        if not strict:
            return True
        if p.pos in ("K", "DEF") and rounds_left >= 2 and rng.random() > 0.05:
            return False
        if rounds_left < len(need_kd) and p.pos not in need_kd:
            return False
        return True

    pool = [p for p in available if ok(p, True)] or [p for p in available if ok(p, False)] \
        or available
    # only the ~25 cheapest by ADP are realistic candidates; keeps this fast
    pool.sort(key=lambda p: p.adp_or_rank)
    best, best_score = None, None
    for p in pool[:25]:
        adp = p.adp_or_rank
        score = adp + rng.gauss(0, noise * (1.5 + 0.08 * adp))
        if best_score is None or score < best_score:
            best, best_score = p, score
    return best


def fill_ppg(players: list[Player]) -> dict:
    """Estimated points per game for every player: real ppg where known,
    otherwise the average of positional neighbours (rookies), else a default."""
    est = {}
    by_pos = {pos: sorted([p for p in players if p.pos == pos], key=lambda p: p.pos_rank)
              for pos in POSITIONS}
    for pos, lst in by_pos.items():
        for i, p in enumerate(lst):
            if p.ppg is not None:
                est[p.key] = p.ppg
                continue
            nb = [q.ppg for q in lst[max(0, i - 3): i + 4] if q.ppg is not None]
            est[p.key] = sum(nb) / len(nb) if nb else max(1.0, POS_DEFAULT_PPG[pos] - 0.1 * i)
    return est


@dataclass
class RosterScore:
    starters: float
    bench: float
    total: float
    lineup: dict


def score_roster(mine: list[Player], league: League, ppg: dict,
                 bench_weight: float = 0.3) -> RosterScore:
    """Best starting lineup by estimated ppg, plus a fraction of the top bench
    RB/WR (injury insurance). Higher is better."""
    pool = sorted(mine, key=lambda p: -ppg.get(p.key, 0.0))
    lineup, used = {}, set()
    for slot, n in league.roster.items():
        if slot in ("BN", "FLEX"):
            continue
        picked = [p for p in pool if p.pos == slot and p.key not in used][:n]
        lineup[slot] = picked
        used.update(p.key for p in picked)
    flex = [p for p in pool if p.pos in league.flex_positions and p.key not in used]
    lineup["FLEX"] = flex[:league.roster.get("FLEX", 0)]
    used.update(p.key for p in lineup["FLEX"])
    starters = sum(ppg.get(p.key, 0.0) for ps in lineup.values() for p in ps)
    bench = [p for p in pool if p.key not in used and p.pos in ("RB", "WR")][:3]
    bench_pts = sum(ppg.get(p.key, 0.0) for p in bench)
    return RosterScore(starters, bench_pts, starters + bench_weight * bench_pts, lineup)


def simulate_draft(players: list[Player], league: League, slot: int, strategy: Strategy,
                   rng: random.Random, noise: float = 1.0) -> list[Player]:
    """Run one full draft; I follow the engine, others follow ADP. Returns my roster."""
    taken: set[str] = set()
    counts_by_slot: dict[int, dict] = {s: {} for s in range(1, league.teams + 1)}
    mine: list[Player] = []
    my_picks = league.picks_for_slot(slot)
    for pick_no in range(1, league.total_picks + 1):
        avail = [p for p in players if p.key not in taken]
        if not avail:
            break
        s = league.slot_of(pick_no)
        rnd = league.round_of(pick_no)
        if s == slot:
            nxt = next((pk for pk in my_picks if pk > pick_no), None)
            recs, _ = engine.recommend(avail, mine, league, strategy, pick_no, nxt, 1)
            choice = recs[0].player if recs else avail[0]
            mine.append(choice)
        else:
            choice = opponent_pick(avail, counts_by_slot[s], rnd, league, rng, noise)
            if choice is None:
                break
        taken.add(choice.key)
        counts_by_slot[s][choice.pos] = counts_by_slot[s].get(choice.pos, 0) + 1
    return mine
