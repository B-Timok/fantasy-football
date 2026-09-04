#!/usr/bin/env python3
"""Convert a pasted, free-form rankings list into data/rankings.csv.

Handles lines like:
  1. Ja'Marr Chase WR CIN
  2) Bijan Robinson, RB, ATL   (tier 1)
  Justin Jefferson (WR - MIN)
  12 Saquon Barkley RB1 PHI 9
A blank line or a line like "Tier 2" / "--- tier 3" starts a new tier.

Usage:  python import_rankings.py my_rankings.txt [-o data/rankings.csv]
        python import_rankings.py --pos RB rb.txt -o data/rankings_RB.csv
"""
import argparse
import csv
import re
import sys

from draftkit.models import normalize_pos

TEAM_RE = re.compile(r"^[A-Z]{2,3}$")
TIER_RE = re.compile(r"^\W*tier\s*(\d+)", re.I)
BYE_RE = re.compile(r"\bbye\s*:?\s*(\d+)", re.I)


def parse_line(line: str, fixed_pos):
    tier_m = TIER_RE.match(line)
    if tier_m:
        return ("tier", int(tier_m.group(1)))
    raw = line.strip()
    if not raw:
        return ("blank", None)
    bye = None
    m = BYE_RE.search(raw)
    if m:
        bye = int(m.group(1))
        raw = BYE_RE.sub("", raw)
    rank = None
    m = re.match(r"^\s*(\d+)[\.\):\-]?\s+(.*)$", raw)
    if m:
        rank, raw = int(m.group(1)), m.group(2)
    # split on whitespace, commas, brackets, pipes, slashes, and " - " (not in-name hyphens)
    toks = [t for t in re.split(r"[\s,()\[\]|/]+|\s-\s", raw) if t]
    pos, prk, team = fixed_pos, None, ""
    name_toks = []
    for t in toks:
        letters, digits = re.sub(r"\d", "", t), re.sub(r"\D", "", t)
        p = normalize_pos(letters) if letters and letters.isupper() else None
        if p and name_toks and (pos is None or p == fixed_pos):
            pos = p
            if digits:
                prk = int(digits)
            continue
        if TEAM_RE.match(t) and name_toks and not team and t not in ("JR", "SR", "II", "III", "IV"):
            team = t
            continue
        if t.isdigit() and name_toks:
            continue  # stray trailing numbers
        if pos is None or p is not None or fixed_pos:
            if not (team and pos):
                name_toks.append(t)
    name = " ".join(name_toks).strip(" ,")
    if not name or pos is None:
        return ("skip", line)
    return ("player", {"rank": rank, "name": name, "pos": pos, "team": team,
                       "pos_rank": prk, "bye": bye})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("infile")
    ap.add_argument("-o", "--out", default="data/rankings.csv")
    ap.add_argument("--pos", help="all players in the file are this position (for per-position files)")
    args = ap.parse_args(argv)
    fixed_pos = normalize_pos(args.pos) if args.pos else None
    with open(args.infile, encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    tier, rows, skipped, n = 1, [], [], 0
    saw_player_in_tier = False
    for line in lines:
        kind, val = parse_line(line, fixed_pos)
        if kind == "tier":
            tier = val
            saw_player_in_tier = False
        elif kind == "blank":
            if saw_player_in_tier:
                tier += 1
                saw_player_in_tier = False
        elif kind == "skip":
            skipped.append(val)
        else:
            n += 1
            val["rank"] = val["rank"] or n
            val["tier"] = tier
            rows.append(val)
            saw_player_in_tier = True
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "name", "pos", "team", "tier", "pos_rank", "bye"])
        for r in rows:
            w.writerow([r["rank"], r["name"], r["pos"], r["team"], r["tier"],
                        r["pos_rank"] or "", r["bye"] or ""])
    print(f"wrote {len(rows)} players to {args.out} ({tier} tiers)")
    if skipped:
        print(f"skipped {len(skipped)} lines (no position found):", file=sys.stderr)
        for s in skipped[:10]:
            print("   " + s, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
