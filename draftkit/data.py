"""Loading rankings / ADP / league config from CSV and JSON."""
from __future__ import annotations

import csv
import json
import os
from typing import Optional

from .models import League, Player, normalize_name, normalize_pos, POSITIONS

# Accepted header aliases (lower-cased, spaces/underscores removed).
ALIASES = {
    "rank": {"rank", "overall", "ovr", "rk", "overallrank", "ecr", "avg"},
    "name": {"name", "player", "playername", "player_name"},
    "pos": {"pos", "position"},
    "team": {"team", "tm", "nfl"},
    "tier": {"tier", "tiers"},
    "adp": {"adp", "avgpick", "averagedraftposition", "sleeperadp"},
    "bye": {"bye", "byeweek"},
    "pos_rank": {"posrank", "positionrank", "prk", "positionalrank"},
    "ppg": {"ppg", "pts", "points", "fpts", "pprpoints", "proj", "projection"},
}


def _canon(header: str) -> Optional[str]:
    h = header.strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    for canon, names in ALIASES.items():
        if h in names:
            return canon
    return None


def _to_int(v) -> Optional[int]:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def _to_float(v) -> Optional[float]:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _read_rows(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return []
    header = rows[0]
    mapping = {i: _canon(h) for i, h in enumerate(header)}
    if "name" not in mapping.values():
        raise ValueError(f"{path}: could not find a player-name column (headers: {header})")
    out = []
    for r in rows[1:]:
        d = {}
        for i, cell in enumerate(r):
            key = mapping.get(i)
            if key:
                d[key] = cell.strip()
        out.append(d)
    return out


def _split_pos_field(pos_field: str) -> tuple[Optional[str], Optional[int]]:
    """'RB12' -> ('RB', 12); 'WR' -> ('WR', None)."""
    if not pos_field:
        return None, None
    letters = "".join(ch for ch in pos_field if not ch.isdigit())
    digits = "".join(ch for ch in pos_field if ch.isdigit())
    return normalize_pos(letters), (int(digits) if digits else None)


def load_rankings(path: str) -> list[Player]:
    """Load the overall rankings CSV. Only a name column is required; position
    is strongly recommended. Rank defaults to row order."""
    rows = _read_rows(path)
    players: list[Player] = []
    seen: set[str] = set()
    for i, d in enumerate(rows, start=1):
        name = d.get("name", "")
        if not name:
            continue
        pos, embedded_prk = _split_pos_field(d.get("pos", ""))
        if pos is None:
            continue  # skip unknown positions (IDP etc.)
        rank = _to_int(d.get("rank")) or i
        key = normalize_name(name)
        if key in seen:
            continue
        seen.add(key)
        players.append(Player(
            name=name, pos=pos, rank=rank, team=d.get("team", "").upper(),
            pos_rank=_to_int(d.get("pos_rank")) or embedded_prk or 0,
            tier=_to_int(d.get("tier")), adp=_to_float(d.get("adp")),
            bye=_to_int(d.get("bye")), ppg=_to_float(d.get("ppg")),
        ))
    players.sort(key=lambda p: p.rank)
    # re-number ranks densely and fill positional ranks
    counters = {p: 0 for p in POSITIONS}
    for n, p in enumerate(players, start=1):
        p.rank = n
        counters[p.pos] += 1
        if not p.pos_rank:
            p.pos_rank = counters[p.pos]
    return players


def apply_positional_rankings(players: list[Player], data_dir: str) -> list[str]:
    """Optional per-position files: data/rankings_RB.csv etc. They override
    pos_rank and tier. Players not in the overall list are appended to the end."""
    notes = []
    by_key = {p.key: p for p in players}
    for pos in POSITIONS:
        path = os.path.join(data_dir, f"rankings_{pos}.csv")
        if not os.path.exists(path):
            continue
        rows = _read_rows(path)
        n_new = 0
        for i, d in enumerate(rows, start=1):
            name = d.get("name", "")
            if not name:
                continue
            key = normalize_name(name)
            prk = _to_int(d.get("rank")) or _to_int(d.get("pos_rank")) or i
            tier = _to_int(d.get("tier"))
            p = by_key.get(key)
            if p is None:
                p = Player(name=name, pos=pos, rank=len(players) + 1,
                           team=d.get("team", "").upper(), adp=_to_float(d.get("adp")),
                           bye=_to_int(d.get("bye")))
                players.append(p)
                by_key[key] = p
                n_new += 1
            p.pos_rank = prk
            if tier is not None:
                p.tier = tier
            if _to_float(d.get("ppg")) is not None:
                p.ppg = _to_float(d.get("ppg"))
        notes.append(f"{os.path.basename(path)}: {len(rows)} rows, {n_new} new players")
    return notes


def apply_adp(players: list[Player], path: str) -> int:
    """Merge an ADP CSV (name, adp[, pos, team]) into players.

    Matching order: exact normalized name; last name + first initial (so
    "J. Gibbs" matches Jahmyr Gibbs); unique last name. Position narrows each
    step when the CSV has it. Returns number matched."""
    if not path or not os.path.exists(path):
        return 0
    rows = _read_rows(path)
    by_key: dict[str, list[Player]] = {}
    by_last: dict[str, list[Player]] = {}
    teams = {p.team for p in players if p.team}
    for p in players:
        by_key.setdefault(p.key, []).append(p)
        by_last.setdefault(p.key.split()[-1], []).append(p)
    matched = 0
    for d in rows:
        adp = _to_float(d.get("adp")) or _to_float(d.get("rank"))
        if adp is None:
            continue
        raw = d.get("name", "").strip()
        # drop a trailing team code that got glued onto the name ("J. Gibbs DET")
        toks = raw.split()
        if len(toks) > 1 and toks[-1].upper() in teams and toks[-1].isupper():
            toks = toks[:-1]
        key = normalize_name(" ".join(toks))
        if not key:
            continue
        parts = key.split()
        pos, _ = _split_pos_field(d.get("pos", ""))

        def narrow(cands):
            return [c for c in cands if c.pos == pos] if pos else cands

        cands = narrow(by_key.get(key, []))
        if not cands and len(parts) >= 2:
            # "j gibbs" -> Jahmyr Gibbs; "a brown" -> A.J. Brown; "a st brown" -> Amon-Ra St. Brown
            tail, initial = parts[1:], parts[0][0]
            pool = [c for c in narrow(by_last.get(parts[-1], [])) if c.key[0] == initial]
            cands = [c for c in pool if c.key.split()[1:] == tail]          # same shape
            if not cands:
                cands = [c for c in pool if c.key.endswith(" " + " ".join(tail))]
        if not cands:
            cands = narrow(by_last.get(parts[-1], []))
            if len(cands) != 1:
                cands = []
        if not cands and pos == "DEF":
            team = (d.get("team") or "").upper()
            cands = [c for c in players if c.pos == "DEF" and
                     (c.team == team or parts[-1] in c.key.split())]
        if len(cands) == 1 and cands[0].adp is None:
            cands[0].adp = adp
            matched += 1
    return matched


def load_league(path: str) -> League:
    if not path or not os.path.exists(path):
        return League()
    with open(path, encoding="utf-8") as f:
        return League.from_dict(json.load(f))


def write_sample_rankings(path: str, n_per_pos: Optional[dict] = None) -> None:
    """Write a synthetic rankings file so the tool can be demoed before real
    rankings exist. Names are obviously fake."""
    n_per_pos = n_per_pos or {"QB": 24, "RB": 60, "WR": 70, "TE": 24, "K": 14, "DEF": 14}
    # a rough overall ordering: interleave positions with typical PPR spacing
    spacing = {"RB": 1.0, "WR": 1.05, "TE": 2.6, "QB": 3.0, "K": 14.0, "DEF": 13.0}
    offset = {"RB": 0, "WR": 0.4, "TE": 3.0, "QB": 5.0, "K": 165, "DEF": 155}
    rows = []
    for pos, n in n_per_pos.items():
        for i in range(1, n + 1):
            score = offset[pos] + i * spacing[pos] + (i ** 1.35) * 0.35
            tier = min(8, 1 + (i - 1) // (3 if pos in ("QB", "TE") else 6))
            rows.append((score, f"{pos}{i} Sample", pos, "FA", tier))
    rows.sort()
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "name", "pos", "team", "tier", "adp", "bye"])
        for r, (score, name, pos, team, tier) in enumerate(rows, start=1):
            noise = (sum(ord(c) for c in name) % 7) - 3
            adp = round(r + (r * 0.08) * noise / 3, 1)
            w.writerow([r, name, pos, team, tier, max(1.0, adp), ""])
