#!/usr/bin/env python3
"""Build data/rankings.csv from data/overall.txt + the per-position files.

Each line of overall.txt is a player (fuzzy-matched against the positional
CSVs) or "Tier X". Players in the positional files but not in overall.txt are
appended afterwards, ordered by positional tier then points per game, with K
and DEF last -- so the tool still works while the overall list is partial.
"""
import csv
import os
import sys

from draftkit.data import _read_rows
from draftkit.models import POSITIONS, normalize_name, normalize_pos

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TIER_NUM = {"S": 1, "A": 2, "B": 3, "C": 4, "D": 5, "E": 6, "F": 7, "G": 8, "H": 9,
            "I": 10, "J": 11}


def load_positional():
    players = []
    for pos in POSITIONS:
        path = os.path.join(DATA, f"rankings_{pos}.csv")
        if not os.path.exists(path):
            continue
        for d in _read_rows(path):
            d["pos"] = pos
            d["key"] = normalize_name(d["name"])
            d["tier"] = int(d.get("tier") or 0)
            d["pos_rank"] = int(d.get("rank") or 0)
            players.append(d)
    return players


def resolve(line, players):
    toks = line.split()
    pos = team = None
    core = []
    teams = {p.get("team", "").upper() for p in players}
    for t in toks:
        if t.isalpha() and t.isupper() and normalize_pos(t) and len(t) <= 3:
            pos = normalize_pos(t)
        elif t.isupper() and t in teams and len(core) > 0:
            team = t
        else:
            core.append(t)
    q = normalize_name(" ".join(core))
    pool = [p for p in players if (pos is None or p["pos"] == pos)
            and (team is None or p.get("team", "").upper() == team)]
    exact = [p for p in pool if p["key"] == q]
    if len(exact) == 1:
        return exact[0], None
    last = q.split()[-1] if q else ""
    by_last = [p for p in pool if p["key"].split()[-1] == last or p["key"].endswith(q)]
    if len(by_last) == 1:
        return by_last[0], None
    if not by_last:
        return None, f"no match for '{line}'"
    return None, f"ambiguous '{line}': " + ", ".join(
        f"{p['name']} ({p['pos']} {p.get('team', '')})" for p in by_last)


def load_ds_ranks(players):
    """draftsharks rank per player key, for ordering the unlisted tail."""
    path = os.path.join(DATA, "draftsharks.csv")
    if not os.path.exists(path):
        return {}
    from draftkit.data import PlayerIndex
    from draftkit.models import Player
    objs = [Player(name=p["name"], pos=p["pos"], rank=i + 1, team=p.get("team", ""))
            for i, p in enumerate(players)]
    idx = PlayerIndex(objs)
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        for d in csv.DictReader(f):
            hit = idx.find(d["name"], normalize_pos(d["pos"]), d.get("team", ""))
            if hit is not None and hit.key not in out:
                out[hit.key] = int(d["ds_rank"])
    return out


def main():
    players = load_positional()
    ds = load_ds_ranks(players)
    out, problems, used = [], [], set()
    tier = 0
    with open(os.path.join(DATA, "overall.txt"), encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("tier"):
                lab = line.split()[-1].upper()
                tier = TIER_NUM.get(lab, int(lab) if lab.isdigit() else tier + 1)
                continue
            p, err = resolve(line, players)
            if err:
                problems.append(err)
                continue
            if p["key"] in used:
                problems.append(f"duplicate: {p['name']}")
                continue
            used.add(p["key"])
            out.append((p, tier))
    n_listed = len(out)
    # append the rest: skill positions by tier then ppg, then K, then DEF
    rest = [p for p in players if p["key"] not in used]

    def sort_key(p):
        # draftsharks order first; anything they don't rank goes after, by tier then ppg
        if p["key"] in ds:
            return (0, ds[p["key"]], 0, 0)
        grp = {"K": 1, "DEF": 2}.get(p["pos"], 0)
        ppg = float(p.get("ppg") or 0)
        return (1, grp, p["tier"], -ppg)
    for p in sorted(rest, key=sort_key):
        out.append((p, ""))
    path = os.path.join(DATA, "rankings.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "name", "pos", "team", "tier", "ovr_tier", "pos_rank", "ppg"])
        for r, (p, ovr_tier) in enumerate(out, 1):
            w.writerow([r, p["name"], p["pos"], p.get("team", ""), p["tier"], ovr_tier,
                        p["pos_rank"], p.get("ppg", "")])
    n_ds = sum(1 for p in rest if p["key"] in ds)
    print(f"wrote {len(out)} players to {path}: {n_listed} from overall.txt, "
          f"{len(rest)} appended ({n_ds} ordered by draftsharks rank, rest by tier)")
    for pr in problems:
        print("  PROBLEM: " + pr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
