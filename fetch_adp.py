#!/usr/bin/env python3
"""Get ADP into data/adp.csv from a web page or from pasted text.

  python fetch_adp.py https://www.draftsharks.com/adp/ppr/cbs-consensus-espn-sleeper/12
  python fetch_adp.py --text pasted.txt        # text copied from any ADP table

The HTML mode looks for a table whose header has an "ADP" column and a
player/name column. The text mode scans for "<name> <POS> <TEAM> ... <number>"
patterns, so a copy-paste of a table works even when the layout is messy.
"""
import argparse
import csv
import os
import re
import sys
import urllib.request
from html.parser import HTMLParser

from draftkit.models import normalize_pos

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "adp.csv")
POS_RE = r"(QB|RB|WR|TE|K|PK|DST|DEF|D/ST)"
NAME_RE = r"([A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+){0,3}(?:\s+(?:Jr|Sr|II|III|IV)\.?)?)"


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables, self._row, self._cell, self._in_cell = [], None, [], False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._in_cell, self._cell = True, []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None:
            self._row.append(" ".join(" ".join(self._cell).split()))
            self._in_cell = False
        elif tag == "tr" and self._row is not None and self.tables:
            self.tables[-1].append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._in_cell:
            self._cell.append(data)


def rows_from_html(html):
    tp = TableParser()
    tp.feed(html)
    best = []
    for table in tp.tables:
        if not table:
            continue
        header = [h.lower() for h in table[0]]
        adp_col = next((i for i, h in enumerate(header) if "adp" in h or "avg" in h), None)
        name_col = next((i for i, h in enumerate(header) if "player" in h or "name" in h), None)
        if adp_col is None or name_col is None:
            continue
        pos_col = next((i for i, h in enumerate(header) if h.strip() in ("pos", "position")), None)
        team_col = next((i for i, h in enumerate(header) if h.strip() in ("team", "tm")), None)
        out = []
        for r in table[1:]:
            if len(r) <= max(adp_col, name_col):
                continue
            m = re.search(r"\d+(?:\.\d+)?", r[adp_col])
            if not m:
                continue
            cell = r[name_col]
            pos = normalize_pos(r[pos_col]) if pos_col is not None and pos_col < len(r) else None
            team = r[team_col] if team_col is not None and team_col < len(r) else ""
            # name cell may be "Ja'Marr Chase WR CIN" or "Ja'Marr Chase (CIN - WR)"
            if pos is None:
                pm = re.search(r"\b" + POS_RE + r"\b", cell)
                if pm:
                    pos = normalize_pos(pm.group(1))
                    cell = cell[:pm.start()]
            name = re.split(r"[(\-|,]", cell)[0].strip()
            out.append((name, pos or "", team, float(m.group(0))))
        if len(out) > len(best):
            best = out
    return best


def rows_from_text(text):
    text = text.replace(" ", " ")
    pat = re.compile(NAME_RE + r"[\s,|()\-]*" + POS_RE + r"\d*\b[\s,|()\-]*([A-Z]{2,3})?\b[^\n\d]*?(\d{1,3}(?:\.\d+)?)")
    out, seen = [], set()
    for m in pat.finditer(text):
        name, pos, team, adp = m.group(1).strip(), normalize_pos(m.group(2)), m.group(3) or "", float(m.group(4))
        if name.lower() in seen or name.lower() in ("player", "name"):
            continue
        seen.add(name.lower())
        out.append((name, pos, team, adp))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="URL, or a file path with --text")
    ap.add_argument("--text", action="store_true", help="source is a text file of pasted rows")
    ap.add_argument("-o", "--out", default=OUT)
    a = ap.parse_args()
    if a.text:
        with open(a.source, encoding="utf-8-sig") as f:
            rows = rows_from_text(f.read())
    else:
        req = urllib.request.Request(a.source, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", "replace")
        rows = rows_from_html(html) or rows_from_text(re.sub(r"<[^>]+>", " ", html))
    if not rows:
        print("Found no ADP rows. Copy the table text into a file and run with --text.")
        return 1
    rows.sort(key=lambda r: r[3])
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["name", "pos", "team", "adp"])
        w.writerows(rows)
    print(f"wrote {len(rows)} players to {a.out}; first: {rows[0][0]} ({rows[0][3]}), "
          f"last: {rows[-1][0]} ({rows[-1][3]})")
    # report how well it lines up with the rankings
    rk = os.path.join(os.path.dirname(a.out), "rankings.csv")
    if os.path.exists(rk):
        from draftkit.data import apply_adp, apply_positional_rankings, load_rankings
        players = load_rankings(rk)
        apply_positional_rankings(players, os.path.dirname(rk))
        apply_adp(players, a.out)
        missing = [p for p in players if p.adp is None and p.rank <= 120]
        have = sum(1 for p in players if p.adp is not None)
        print(f"ADP matched for {have}/{len(players)} ranked players.")
        if missing:
            print("No ADP for these top-120 players (check spelling in adp.csv):")
            for p in missing:
                print(f"   rk {p.rank:>3} {p.name} ({p.pos} {p.team})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
