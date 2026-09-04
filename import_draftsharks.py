#!/usr/bin/env python3
"""Parse a copy-pasted DraftSharks rankings table into data/draftsharks.csv.

Row layout in the paste (one field per line, then a tab-separated stats line):
    <rank> / <TEAM> logo / <Name> / <TEAM> / <POS><posrank> / <blank>
    <games> <adp r.pp> <bye> <sos> <injury%> <floor> <cons proj> <ds proj> <ceil> <3D value>
"Tier N" lines mark DraftSharks' tiers.

  python import_draftsharks.py data/draftsharks.txt
"""
import csv
import os
import re
import sys

from draftkit.models import normalize_pos

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "draftsharks.csv")
COLS = ["ds_rank", "name", "pos", "team", "pos_rank", "ds_tier", "games", "adp", "bye", "sos",
        "injury", "floor", "cons_proj", "proj", "ceil", "value"]


def rp_to_pick(s: str, teams: int = 12):
    """'2.05' -> 17 ; '0' or '-' -> None."""
    m = re.fullmatch(r"(\d+)\.(\d+)", s.strip())
    if not m:
        return None
    r, p = int(m.group(1)), int(m.group(2))
    return (r - 1) * teams + p if r >= 1 else None


def parse(text: str):
    lines = [ln.rstrip("\n") for ln in text.splitlines()]
    rows, tier, i = [], 0, 0
    while i < len(lines):
        ln = lines[i].strip()
        m = re.fullmatch(r"Tier\s+(\d+)", ln, re.I)
        if m:
            tier = int(m.group(1))
            i += 1
            continue
        if ln.isdigit() and i + 4 < len(lines) and lines[i + 1].strip().endswith("logo"):
            rank = int(ln)
            name = lines[i + 2].strip()
            team = lines[i + 3].strip()
            posf = lines[i + 4].strip()
            pos = normalize_pos(re.sub(r"\d", "", posf))
            prk = int(re.sub(r"\D", "", posf) or 0)
            # stats line: first tab-separated line after this
            j = i + 5
            while j < len(lines) and "\t" not in lines[j]:
                j += 1
            stats = lines[j].split("\t") if j < len(lines) else []
            i = j + 1
            if pos is None or len(stats) < 10:
                continue

            def num(s):
                s = s.strip().rstrip("%")
                try:
                    return float(s)
                except ValueError:
                    return None
            inj = num(stats[4])
            rows.append({
                "ds_rank": rank, "name": name, "pos": pos, "team": team, "pos_rank": prk,
                "ds_tier": tier, "games": num(stats[0]), "adp": rp_to_pick(stats[1]),
                "bye": num(stats[2]), "sos": num(stats[3]),
                "injury": inj / 100.0 if inj is not None else None,
                "floor": num(stats[5]), "cons_proj": num(stats[6]), "proj": num(stats[7]),
                "ceil": num(stats[8]), "value": num(stats[9]),
            })
            continue
        i += 1
    return rows


def main(argv=None):
    argv = argv or sys.argv[1:]
    src = argv[0] if argv else os.path.join(os.path.dirname(OUT), "draftsharks.txt")
    with open(src, encoding="utf-8-sig") as f:
        rows = parse(f.read())
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r[k] is None else r[k]) for k in COLS})
    print(f"wrote {len(rows)} players to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
