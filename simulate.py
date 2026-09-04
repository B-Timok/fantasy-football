#!/usr/bin/env python3
"""Run many simulated drafts to (a) compare strategies for each draft slot and
(b) tune the engine's weights. Opponents draft by ADP with noise; you follow
the engine. Rosters are scored by estimated points per game of the best
starting lineup plus a little bench credit.

  python simulate.py                       # strategy x slot table, 60 drafts per cell
  python simulate.py --sims 200 --slots 7  # one slot, more drafts
  python simulate.py --tune --sims 40      # random search over engine weights
"""
import argparse
import json
import multiprocessing as mp
import os
import random
import statistics
import sys
import time

from draftkit import engine, sim, strategies
from draftkit.data import (add_unranked, apply_adp, apply_adp_full, apply_positional_rankings,
                           load_league, load_rankings)
from draftkit.models import League

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

_PLAYERS = _LEAGUE = _PPG = None


def load_all():
    league = load_league(os.path.join(DATA, "league.json"))
    players = load_rankings(os.path.join(DATA, "rankings.csv"))
    apply_positional_rankings(players, DATA)
    _, unmatched = apply_adp_full(players, os.path.join(DATA, "adp.csv"))
    add_unranked(players, unmatched)
    ov = os.path.join(DATA, "adp_overrides.csv")
    if os.path.exists(ov):
        apply_adp(players, ov, replace=True)
    return players, league, sim.fill_ppg(players)


def _init(params):
    global _PLAYERS, _LEAGUE, _PPG
    _PLAYERS, _LEAGUE, _PPG = load_all()
    if params:
        engine.set_params(params)


def _run_cell(args):
    slot, strat_name, sims, seed, noise = args
    rng = random.Random(seed)
    strat = strategies.get(strat_name)
    scores = []
    for _ in range(sims):
        mine = sim.simulate_draft(_PLAYERS, _LEAGUE, slot, strat, rng, noise)
        scores.append(sim.score_roster(mine, _LEAGUE, _PPG).total)
    return slot, strat_name, statistics.mean(scores), statistics.pstdev(scores)


def run_table(slots, strat_names, sims, workers, params=None, noise=1.0, seed=0, quiet=False):
    jobs = [(s, n, sims, seed * 1000 + s * 17 + i, noise)
            for s in slots for i, n in enumerate(strat_names)]
    t0 = time.time()
    with mp.Pool(workers, initializer=_init, initargs=(params,)) as pool:
        results = pool.map(_run_cell, jobs)
    table = {}
    for slot, name, mean, sd in results:
        table.setdefault(slot, {})[name] = (mean, sd)
    if not quiet:
        print(f"\n{sims} drafts per cell, {len(jobs)} cells, {time.time() - t0:.0f}s. "
              f"Score = est. starting-lineup ppg + 0.3 x top-3 bench RB/WR ppg.\n")
        print(f"{'slot':>4} " + " ".join(f"{n:>13}" for n in strat_names) + "   best")
        for slot in slots:
            row = table[slot]
            best = max(row, key=lambda n: row[n][0])
            print(f"{slot:>4} " + " ".join(
                f"{row[n][0]:>8.1f}±{row[n][1]:<4.1f}" for n in strat_names) + f"   {best}")
    return table


def tune(slots, sims, workers, trials, seed):
    """Random search over a few engine weights, scored across all slots with
    each slot's best strategy. Writes data/engine_params.json."""
    rng = random.Random(seed)
    space = {
        "URGENCY_W": lambda: rng.choice([0.4, 0.6, 0.8, 1.0, 1.3]),
        "FLEX_DISCOUNT": lambda: rng.choice([0.85, 0.92, 1.0]),
        "BENCH_RBWR": lambda: rng.choice([0.6, 0.7, 0.8, 0.9]),
        "BENCH_QBTE": lambda: rng.choice([0.3, 0.45, 0.6]),
        "LAST_IN_TIER_BONUS": lambda: rng.choice([0.0, 2.0, 4.0, 7.0]),
    }
    names = list(strategies.STRATEGIES)
    best_params, best_score, history = None, None, []
    base = engine.get_params()
    for t in range(trials):
        cand = dict(base)
        if t > 0:
            for k, f in space.items():
                cand[k] = f()
        table = run_table(slots, names, sims, workers, cand, seed=seed + t, quiet=True)
        # each slot uses its best strategy; average across slots
        score = statistics.mean(max(v[0] for v in table[s].values()) for s in slots)
        history.append((score, cand))
        tag = "base" if t == 0 else f"trial {t}"
        print(f"{tag:>8}: {score:.2f}   " + ", ".join(f"{k}={cand[k]}" for k in space))
        if best_score is None or score > best_score:
            best_params, best_score = cand, score
    out = os.path.join(DATA, "engine_params.json")
    with open(out, "w") as f:
        json.dump(best_params, f, indent=1)
    print(f"\nbest {best_score:.2f} -> wrote {out}")
    return best_params


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=60, help="drafts per slot/strategy cell")
    ap.add_argument("--slots", default="1-12", help="e.g. 7 or 1-12 or 1,5,12")
    ap.add_argument("--strategies", default="all")
    ap.add_argument("--workers", type=int, default=max(1, mp.cpu_count()))
    ap.add_argument("--noise", type=float, default=1.0, help="opponent ADP noise multiplier")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--tune", action="store_true", help="random-search engine weights")
    ap.add_argument("--trials", type=int, default=12)
    a = ap.parse_args()
    slots = []
    for part in a.slots.split(","):
        if "-" in part:
            lo, hi = part.split("-")
            slots += list(range(int(lo), int(hi) + 1))
        else:
            slots.append(int(part))
    names = list(strategies.STRATEGIES) if a.strategies == "all" else a.strategies.split(",")
    params_path = os.path.join(DATA, "engine_params.json")
    params = json.load(open(params_path)) if os.path.exists(params_path) else None
    if a.tune:
        tune(slots, a.sims, a.workers, a.trials, a.seed)
        return 0
    table = run_table(slots, names, a.sims, a.workers, params, a.noise, a.seed)
    out = os.path.join(DATA, "sim_results.json")
    with open(out, "w") as f:
        json.dump({str(s): {n: {"mean": v[0], "sd": v[1]} for n, v in row.items()}
                   for s, row in table.items()}, f, indent=1)
    print(f"\nwrote {out} (draft.py uses it to suggest a strategy for your slot)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
