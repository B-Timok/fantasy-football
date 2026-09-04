"""Interactive draft loop."""
from __future__ import annotations

import argparse
import difflib
import json
import os
import random
import shlex
import sys
from typing import Optional

from . import sim, strategies
from .data import (add_unranked, apply_adp, apply_adp_full, apply_draftsharks,
                   apply_positional_rankings, load_league, load_rankings,
                   write_sample_rankings)
from . import engine
from .engine import build_roster, p_available, recommend
from .models import League, Player, POSITIONS, normalize_name, normalize_pos
from .state import DraftState

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")

HELP = """
Commands (case-insensitive, partial names are fine):
  <name>              record that player as taken by whoever is on the clock
  me <name>           record the player as YOUR pick (overrides the clock)
  them <name>         record the player as another team's pick
  x <name> [POS]      record a pick of someone NOT in your rankings (e.g. "x Joe Smith RB")
  undo                take back the last pick
  b / board [POS] [N] best available (optionally one position), default 15
  r / roster          your roster so far
  s / show            re-print recommendations
  next                your upcoming pick numbers
  taken [N]           last N picks
  find <text>         search players (shows taken status)
  strategy [NAME]     show or switch strategy
  slot N              change your draft slot
  sync                pull picks from Sleeper (needs sleeper_draft_id in league.json)
  auto                let the engine make your pick (top recommendation)
  help                this text
  quit                exit (state is saved after every pick anyway)
"""


def suggest_strategy(slot: int, teams: int) -> str:
    """Best strategy for this slot from simulate.py results if present, else a rule of thumb."""
    path = os.path.join(DATA, "sim_results.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                row = json.load(f).get(str(slot), {})
            if row:
                best = max(row, key=lambda n: row[n]["mean"])
                base = row.get("balanced", {}).get("mean")
                # only leave 'balanced' for a clear win; sub-point gaps are noise
                if base is None or row[best]["mean"] - base >= 1.0:
                    return best
                return "balanced"
        except (ValueError, KeyError):
            pass
    return strategies.suggest_for_slot(slot, teams)


class Draft:
    def __init__(self, players: list[Player], league: League, state: DraftState,
                 mock: bool = False, seed: Optional[int] = None):
        self.players = players
        self.by_key = {p.key: p for p in players}
        self.league = league
        self.state = state
        self.strategy = strategies.get(state.strategy)
        self.mock = mock
        self.rng = random.Random(seed)

    # ---- mock opponents ------------------------------------------------
    def mock_pick(self) -> Optional[Player]:
        st, lg = self.state, self.league
        now = st.current_pick
        slot = lg.slot_of(now)
        counts: dict[str, int] = {}
        for pk in st.picks:
            if pk.slot == slot:
                counts[pk.pos] = counts.get(pk.pos, 0) + 1
        return sim.opponent_pick(self.available(), counts, lg.round_of(now), lg, self.rng)

    def run_mock_until_my_pick(self) -> None:
        st, lg = self.state, self.league
        made = []
        while st.current_pick <= lg.total_picks and not st.is_my_pick(lg):
            p = self.mock_pick()
            if p is None:
                break
            pk = st.add_pick(p, lg, mine=False)
            made.append(f"#{pk.pick_no} T{pk.slot} {p.name} ({p.pos}{p.pos_rank})")
        if made:
            print("  mock picks: " + "; ".join(made))

    # ---- lookups ------------------------------------------------------
    def available(self) -> list[Player]:
        taken = self.state.taken_keys()
        return [p for p in self.players if p.key not in taken]

    def my_players(self) -> list[Player]:
        return [self.by_key[k] for k in self.state.my_keys() if k in self.by_key]

    def find(self, text: str, only_available: bool = True) -> list[Player]:
        q = normalize_name(text)
        if not q:
            return []
        pool = self.available() if only_available else self.players
        pos_filter = None
        # allow "rb smith" or "smith rb"
        toks = q.split()
        for t in list(toks):
            if t.isalpha() and normalize_pos(t) and len(toks) > 1:
                pos_filter = normalize_pos(t)
                toks.remove(t)
        q = " ".join(toks)
        if pos_filter:
            pool = [p for p in pool if p.pos == pos_filter]
        exact = [p for p in pool if p.key == q]
        if exact:
            return exact
        # a unique last-name match wins outright ("chase" -> Ja'Marr Chase, not Chase Brown)
        last = [p for p in pool if p.key.split()[-1] == q]
        if len(last) == 1:
            return last
        starts = [p for p in pool if p.key.startswith(q)]
        contains = [p for p in pool if q in p.key and p not in starts]
        word = [p for p in pool if any(w.startswith(q) for w in p.key.split())
                and p not in starts and p not in contains]
        hits = starts + contains + word
        if not hits:
            close = difflib.get_close_matches(q, [p.key for p in pool], n=5, cutoff=0.72)
            hits = [p for p in pool if p.key in close]
        hits.sort(key=lambda p: p.rank)
        return hits

    # ---- output helpers --------------------------------------------------
    def header(self) -> str:
        st, lg = self.state, self.league
        cur = st.current_pick
        if cur > lg.total_picks:
            return "Draft complete."
        rnd, slot = lg.round_of(cur), lg.slot_of(cur)
        mine = st.is_my_pick(lg)
        nxt = st.next_my_pick(lg)
        who = "YOU ARE ON THE CLOCK" if mine else f"team {slot} on the clock"
        line = f"Pick {cur}  (round {rnd}, pick {(cur - 1) % lg.teams + 1})  —  {who}."
        if mine:
            if nxt:
                line += f"  Your next pick after this: #{nxt} ({nxt - cur} picks later)."
        elif nxt:
            line += f"  Your pick: #{nxt} ({nxt - cur} picks away)."
        return line

    def roster_line(self) -> str:
        rv = build_roster(self.my_players(), self.league)
        parts = []
        for slot, n in self.league.roster.items():
            if slot == "BN":
                continue
            names = [p.name.split()[-1] for p in rv.starters.get(slot, [])]
            names += ["--"] * (n - len(names))
            parts.append(f"{slot}: {', '.join(names)}")
        bn = [p.name.split()[-1] for p in rv.bench]
        parts.append(f"BN({len(bn)}/{self.league.roster.get('BN', 0)}): {', '.join(bn) or '--'}")
        return " | ".join(parts)

    def show(self, n: int = 10) -> None:
        st, lg = self.state, self.league
        print()
        print(self.header())
        if st.current_pick > lg.total_picks:
            return
        print(f"Strategy: {self.strategy.name}    Roster -> {self.roster_line()}")
        mine = st.is_my_pick(lg)
        now = st.current_pick
        # If it's my pick, "next" = my following pick. If not, evaluate as of my
        # upcoming pick: what survives from now until then.
        if mine:
            target, nxt = now, st.next_my_pick(lg)
        else:
            target = st.next_my_pick(lg)
            nxt = st.next_my_pick(lg, after=target) if target else None
        if target is None:
            print("You have no picks left.")
            return
        avail = self.available()
        recs, outlooks = recommend(avail, self.my_players(), lg, self.strategy, target, nxt,
                                   n * 3 if not mine else n)
        if not mine:
            print(f"(Planning for your pick #{target}. '@yours' = chance he lasts to #{target}; "
                  f"'@next' = chance he lasts to your following pick #{nxt}.)")
        print()
        yours_hdr = f"{'@yours':>6} " if not mine else ""
        print(f"{'#':>2} {'score':>6}  {'player':<26}{'pos':<5}{'rk':>4} {'tier':>4} {'adp':>5} "
              f"{'bye':>3} {yours_hdr}{'@next':>5}  why")
        shown, longshots = 0, []
        for r in recs:
            p = r.player
            p_yours = p_available(p, target, now) if not mine else 1.0
            if not mine and p_yours < 0.10:
                longshots.append(f"{p.name} ({p_yours * 100:.0f}%)")
                continue
            shown += 1
            if shown > n:
                break
            tier = str(p.tier) if p.tier is not None else "-"
            adp = f"{p.adp:.0f}" if p.adp is not None else "-"
            avail_s = f"{r.p_avail_next * 100:.0f}%" if nxt else "-"
            yours_s = f"{p_yours * 100:.0f}% " if not mine else ""
            bye = str(p.bye) if p.bye else "-"
            print(f"{shown:>2} {r.score:>6.1f}  {p.name[:25]:<26}{p.pos + str(p.pos_rank):<5}"
                  f"{p.rank:>4} {tier:>4} {adp:>5} {bye:>3} {yours_s:>6}{avail_s:>5}  "
                  f"{'; '.join(r.reasons)}")
        if longshots:
            print(f"   unlikely to reach you: {', '.join(longshots[:6])}")
        print()
        print(f"{'pos':<4}{'best now':<26}{'likely best at your next pick':<32}"
              f"{'drop':>5} {'~gone':>5}")
        for pos in POSITIONS:
            o = outlooks[pos]
            if o.best_now is None:
                continue
            bn = f"{o.best_now.name[:20]} (rk {o.best_now.rank})"
            ln = (f"{o.likely_next.name[:20]} (rk {o.likely_next.rank})"
                  if o.likely_next and nxt else "-")
            print(f"{pos:<4}{bn:<26}{ln:<32}{o.dropoff:>5.0f} {o.n_expected_gone:>5.1f}")

    def board(self, pos: Optional[str], n: int) -> None:
        avail = self.available()
        if pos:
            avail = [p for p in avail if p.pos == pos]
        print(f"{'rk':>4} {'pos':<5}{'player':<26}{'team':<5}{'tier':>4} {'adp':>5}")
        for p in avail[:n]:
            tier = str(p.tier) if p.tier is not None else "-"
            adp = f"{p.adp:.0f}" if p.adp is not None else "-"
            print(f"{p.rank:>4} {p.pos + str(p.pos_rank):<5}{p.name[:25]:<26}{p.team:<5}"
                  f"{tier:>4} {adp:>5}")

    def roster(self) -> None:
        rv = build_roster(self.my_players(), self.league)
        for slot in self.league.roster:
            if slot == "BN":
                continue
            for p in rv.starters.get(slot, []):
                print(f"  {slot:<5} {p.label()}  rk {p.rank}")
            for _ in range(self.league.roster[slot] - len(rv.starters.get(slot, []))):
                print(f"  {slot:<5} --")
        for p in rv.bench:
            print(f"  BN    {p.label()}  rk {p.rank}")
        print(f"  Picks: {[p.pick_no for p in self.state.picks if p.mine]}")

    # ---- actions ------------------------------------------------------
    def add_unknown(self, text: str, mine: Optional[bool] = None) -> bool:
        toks = text.split()
        pos = "WR"
        if toks and normalize_pos(toks[-1]) and toks[-1].isalpha() and len(toks) > 1:
            pos = normalize_pos(toks[-1])
            toks = toks[:-1]
        name = " ".join(toks).strip()
        if not name:
            print("  usage: x <name> [POS]")
            return False
        p = Player(name=name, pos=pos, rank=len(self.players) + 1)
        if p.key in self.by_key:
            return self.pick(name, mine)
        self.players.append(p)
        self.by_key[p.key] = p
        pk = self.state.add_pick(p, self.league, mine)
        tag = "YOU" if pk.mine else f"team {pk.slot}"
        print(f"  #{pk.pick_no}: {tag} -> {name} ({pos}, unranked)")
        return True

    def pick(self, text: str, mine: Optional[bool]) -> bool:
        if self.state.current_pick > self.league.total_picks:
            print("Draft is complete.")
            return False
        hits = self.find(text)
        if not hits:
            taken = [p for p in self.find(text, only_available=False)]
            if taken:
                print(f"Already taken: {', '.join(p.name for p in taken[:3])}")
                return False
            print(f"No player matches '{text}'.")
            try:
                ans = input("  Record as an unranked player? [y/N] ").strip().lower()
            except EOFError:
                ans = ""
            if ans == "y":
                return self.add_unknown(text, mine)
            return False
        if len(hits) > 1 and not (hits[0].key == normalize_name(text)):
            print("Which one?")
            for i, p in enumerate(hits[:8], 1):
                print(f"  {i}. {p.label()}  rk {p.rank}")
            try:
                ans = input("  number (blank to cancel): ").strip()
            except EOFError:
                ans = ""
            if not ans.isdigit() or not (1 <= int(ans) <= len(hits[:8])):
                print("  cancelled")
                return False
            player = hits[int(ans) - 1]
        else:
            player = hits[0]
        pk = self.state.add_pick(player, self.league, mine)
        tag = "YOU" if pk.mine else f"team {pk.slot}"
        print(f"  #{pk.pick_no}: {tag} -> {player.label()}  (rk {player.rank})")
        return True

    def sync(self) -> None:
        from . import sleeper
        did = self.league.sleeper_draft_id
        if not did:
            print("Set sleeper_draft_id in data/league.json first.")
            return
        try:
            raw = sleeper.fetch_picks(did)
        except Exception as e:  # network, bad id, ...
            print(f"Sleeper fetch failed: {e}")
            return
        picks = sleeper.parse_picks(raw)
        added, unknown = 0, []
        for p in picks:
            if p["pick_no"] != self.state.current_pick:
                continue  # already have it (or gap)
            player = self.by_key.get(p["key"])
            if player is None:
                hits = self.find(p["name"])
                player = hits[0] if len(hits) == 1 else None
            if player is None:
                unknown.append(f"#{p['pick_no']} {p['name']} ({p['pos']})")
                player = Player(name=p["name"], pos=p["pos"] or "WR", rank=len(self.players) + 1)
                self.players.append(player)
                self.by_key[player.key] = player
            self.state.add_pick(player, self.league, mine=(p["slot"] == self.state.slot))
            added += 1
        print(f"Synced {added} new picks from Sleeper (total {len(picks)}).")
        if unknown:
            print("Not in your rankings (added as unranked): " + ", ".join(unknown))

    def auto_pick(self) -> bool:
        st, lg = self.state, self.league
        if st.current_pick > lg.total_picks:
            print("Draft is complete.")
            return False
        recs, _ = recommend(self.available(), self.my_players(), lg, self.strategy,
                            st.current_pick, st.next_my_pick(lg), 1)
        if not recs:
            return False
        return self.pick(recs[0].player.name, mine=st.is_my_pick(lg))

    # ---- loop -----------------------------------------------------------
    def run(self) -> None:
        print(HELP)
        if self.mock:
            print("MOCK MODE: other teams pick automatically by ADP. Type your pick when "
                  "you're on the clock, or 'auto' to let the engine choose.")
            self.run_mock_until_my_pick()
        self.show()
        while True:
            if self.mock and (self.state.current_pick > self.league.total_picks
                              or not self.available()
                              or (not self.state.is_my_pick(self.league)
                                  and self.state.next_my_pick(self.league) is None)):
                print("\nMock draft complete. Your roster:")
                self.roster()
                break
            try:
                line = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            try:
                parts = shlex.split(line)
            except ValueError:
                parts = line.split()
            cmd, rest = parts[0].lower(), " ".join(parts[1:])
            if cmd in ("q", "quit", "exit"):
                break
            elif cmd in ("h", "help", "?"):
                print(HELP)
            elif cmd in ("s", "show"):
                self.show()
            elif cmd == "me":
                if self.pick(rest, mine=True):
                    self.after_pick()
            elif cmd in ("them", "other", "t"):
                if self.pick(rest, mine=False):
                    self.after_pick()
            elif cmd == "x":
                if self.add_unknown(rest, mine=None):
                    self.after_pick()
            elif cmd == "auto":
                if self.auto_pick():
                    self.after_pick()
            elif cmd in ("u", "undo"):
                pk = self.state.undo()
                print(f"  undid #{pk.pick_no} {pk.name}" if pk else "  nothing to undo")
                self.show()
            elif cmd in ("b", "board"):
                pos, n = None, 15
                for a in parts[1:]:
                    if a.isdigit():
                        n = int(a)
                    elif normalize_pos(a):
                        pos = normalize_pos(a)
                self.board(pos, n)
            elif cmd in ("r", "roster"):
                self.roster()
            elif cmd == "next":
                cur = self.state.current_pick
                mine = [pk for pk in self.league.picks_for_slot(self.state.slot) if pk >= cur]
                print("  your remaining picks: " + ", ".join(
                    f"#{pk} (R{self.league.round_of(pk)})" for pk in mine))
            elif cmd == "taken":
                n = int(rest) if rest.isdigit() else 12
                for pk in self.state.picks[-n:]:
                    tag = "YOU" if pk.mine else f"team {pk.slot}"
                    print(f"  #{pk.pick_no:>3} R{self.league.round_of(pk.pick_no):<2} {tag:<8} "
                          f"{pk.name} ({pk.pos})")
            elif cmd in ("f", "find", "search"):
                taken = self.state.taken_keys()
                for p in self.find(rest, only_available=False)[:12]:
                    flag = "TAKEN" if p.key in taken else ""
                    print(f"  rk {p.rank:>3}  {p.label():<34} adp {p.adp or '-'}  {flag}")
            elif cmd in ("strategy", "strat"):
                if rest:
                    try:
                        self.strategy = strategies.get(rest)
                        self.state.strategy = self.strategy.name
                        self.state.save()
                        print(f"  strategy -> {self.strategy.name}: {self.strategy.description}")
                        self.show()
                    except KeyError as e:
                        print(f"  {e}")
                else:
                    for s in strategies.STRATEGIES.values():
                        mark = "*" if s.name == self.strategy.name else " "
                        print(f" {mark} {s.name:<14} {s.description}  [{s.suggested_slots}]")
            elif cmd == "slot" and rest.isdigit():
                self.state.slot = int(rest)
                self.state.save()
                self.show()
            elif cmd == "sync":
                self.sync()
                self.show()
            else:
                # bare player name -> pick for whoever is on the clock
                if self.pick(line, mine=None):
                    self.after_pick()

    def after_pick(self) -> None:
        if self.mock:
            self.run_mock_until_my_pick()
        self.show()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fantasy football draft assistant")
    ap.add_argument("--slot", type=int, help="your draft slot (1..teams)")
    ap.add_argument("--rankings", default=os.path.join(DATA, "rankings.csv"))
    ap.add_argument("--adp", default=os.path.join(DATA, "adp.csv"),
                    help="optional ADP csv (name, adp)")
    ap.add_argument("--league", default=os.path.join(DATA, "league.json"))
    ap.add_argument("--state", default=None,
                    help="draft state file (default data/draft_state.json, or "
                         "data/mock_state.json with --mock)")
    ap.add_argument("--strategy", help="starting strategy (see 'strategy' command)")
    ap.add_argument("--new", action="store_true", help="discard saved draft state and start over")
    ap.add_argument("--sample", action="store_true",
                    help="write data/rankings.sample.csv with fake players and use it")
    ap.add_argument("--mock", action="store_true",
                    help="practice: the other teams draft automatically by ADP")
    ap.add_argument("--seed", type=int, help="random seed for --mock (repeatable drafts)")
    args = ap.parse_args(argv)
    if args.state is None:
        args.state = os.path.join(DATA, "mock_state.json" if args.mock else "draft_state.json")

    league = load_league(args.league)
    params_path = os.path.join(DATA, "engine_params.json")
    if os.path.exists(params_path):
        with open(params_path) as f:
            engine.set_params(json.load(f))
        print(f"Using tuned weights from {os.path.basename(params_path)}")
    rankings_path = args.rankings
    if args.sample:
        rankings_path = os.path.join(DATA, "rankings.sample.csv")
        write_sample_rankings(rankings_path)
        print(f"wrote {rankings_path}")
    if not os.path.exists(rankings_path):
        print(f"No rankings file at {rankings_path}. Put your rankings there "
              f"(columns: rank,name,pos,team,tier,adp,bye) or run with --sample to demo.")
        return 1
    players = load_rankings(rankings_path)
    notes = apply_positional_rankings(players, os.path.dirname(rankings_path))
    n_adp, unmatched = apply_adp_full(players, args.adp)
    ds_path = os.path.join(os.path.dirname(args.adp), "draftsharks.csv")
    n_ds, ds_unmatched = apply_draftsharks(players, ds_path)
    if n_ds:
        print(f"Projections/injury/bye for {n_ds} players from draftsharks.csv")
    n_extra = add_unranked(players, ds_unmatched + unmatched)
    if n_extra:
        print(f"Added {n_extra} unranked players (typeable, never recommended)")
    overrides = os.path.join(os.path.dirname(args.adp), "adp_overrides.csv")
    if os.path.exists(overrides):
        n_over = apply_adp(players, overrides, replace=True)
        if n_over:
            print(f"Applied {n_over} ADP overrides from {os.path.basename(overrides)}")
    have_adp = sum(1 for p in players if p.adp is not None)
    print(f"Loaded {len(players)} players from {os.path.basename(rankings_path)}"
          + (f"; ADP for {have_adp}" if have_adp else "; no ADP (using your ranks as ADP)"))
    for n in notes:
        print("  " + n)

    state = None if args.new else DraftState.load(args.state)
    if state is None:
        slot = args.slot
        while not slot:
            ans = input(f"Your draft slot (1-{league.teams}): ").strip()
            slot = int(ans) if ans.isdigit() else 0
        strat = args.strategy or suggest_strategy(slot, league.teams)
        state = DraftState(slot=slot, strategy=strategies.get(strat).name, path=args.state)
        state.save()
        print(f"New draft: slot {slot}, strategy {state.strategy} "
              f"(suggested for that slot; change with 'strategy NAME').")
    else:
        if args.slot:
            state.slot = args.slot
        if args.strategy:
            state.strategy = strategies.get(args.strategy).name
        state.save()
        print(f"Resumed draft from {args.state}: {len(state.picks)} picks recorded, "
              f"slot {state.slot}, strategy {state.strategy}. (--new to start over)")

    Draft(players, league, state, mock=args.mock, seed=args.seed).run()
    return 0
