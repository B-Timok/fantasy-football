import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from draftkit import strategies
from draftkit.data import apply_adp, load_rankings, write_sample_rankings
from draftkit.engine import (base_value, build_roster, p_available, position_outlook,
                             recommend)
from draftkit.models import League, Player, normalize_name, normalize_pos
from draftkit.sleeper import parse_picks, slot_for_user
from draftkit.state import DraftState
from draftkit.cli import Draft


class TestNames(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_name("A.J. Brown Jr."), "aj brown")
        self.assertEqual(normalize_name("Ja'Marr Chase"), "jamarr chase")
        self.assertEqual(normalize_name("Amon-Ra St. Brown"), "amon ra st brown")
        self.assertEqual(normalize_name("Kenneth Walker III"), "kenneth walker")

    def test_pos(self):
        self.assertEqual(normalize_pos("D/ST"), "DEF")
        self.assertEqual(normalize_pos("dst"), "DEF")
        self.assertEqual(normalize_pos("PK"), "K")
        self.assertEqual(normalize_pos("wr"), "WR")
        self.assertIsNone(normalize_pos("LB"))


class TestLeague(unittest.TestCase):
    def test_snake(self):
        lg = League(teams=12, rounds=15)
        self.assertEqual(lg.slot_of(1), 1)
        self.assertEqual(lg.slot_of(12), 12)
        self.assertEqual(lg.slot_of(13), 12)
        self.assertEqual(lg.slot_of(24), 1)
        self.assertEqual(lg.slot_of(25), 1)
        self.assertEqual(lg.picks_for_slot(5)[:4], [5, 20, 29, 44])
        self.assertEqual(lg.picks_for_slot(1)[:3], [1, 24, 25])
        self.assertEqual(len(lg.picks_for_slot(12)), 15)

    def test_from_dict_defaults_rounds(self):
        lg = League.from_dict({"teams": 10, "roster": {"QB": 1, "RB": 2, "BN": 3}})
        self.assertEqual(lg.rounds, 6)


class TestAvailability(unittest.TestCase):
    def test_top_pick_is_gone(self):
        p = Player("A", "RB", 1, adp=1.0)
        self.assertLess(p_available(p, 5, 1), 0.10)
        self.assertGreater(p_available(p, 1, 1), 0.99)

    def test_monotone_in_distance(self):
        p = Player("B", "WR", 30, adp=30.0)
        probs = [p_available(p, 20 + d, 20) for d in range(0, 30, 3)]
        self.assertEqual(probs, sorted(probs, reverse=True))
        self.assertGreater(probs[0], 0.99)

    def test_conditional_on_still_available(self):
        # a player with ADP 20 still on the board at pick 30 isn't "already gone"
        p = Player("C", "RB", 20, adp=20.0)
        self.assertGreater(p_available(p, 31, 30), 0.5)

    def test_value_curve(self):
        self.assertAlmostEqual(base_value(1), 100.0)
        self.assertGreater(base_value(1) - base_value(12), base_value(100) - base_value(111))
        self.assertEqual(base_value(500), 0.0)


def _players():
    write_path = tempfile.mktemp(suffix=".csv")
    write_sample_rankings(write_path)
    ps = load_rankings(write_path)
    os.remove(write_path)
    return ps


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.players = _players()
        self.lg = League()

    def test_positional_ranks_filled(self):
        rbs = [p for p in self.players if p.pos == "RB"]
        self.assertEqual([p.pos_rank for p in rbs[:3]], [1, 2, 3])

    def test_no_kicker_early(self):
        recs, _ = recommend(self.players, [], self.lg, strategies.get("bpa"), 5, 20, top_n=50)
        self.assertFalse(any(r.player.pos in ("K", "DEF") for r in recs))

    def test_kicker_late_when_needed(self):
        mine = [p for p in self.players if p.pos in ("RB", "WR")][:11] + \
               [p for p in self.players if p.pos == "QB"][:1] + \
               [p for p in self.players if p.pos == "TE"][:1]
        # pick 173 = round 15 for slot 5
        recs, _ = recommend([p for p in self.players if p not in mine], mine, self.lg,
                            strategies.get("bpa"), 173, None, top_n=3)
        self.assertIn(recs[0].player.pos, ("K", "DEF"))

    def test_need_matters(self):
        # with 2 RB + FLEX filled by RBs, an equally ranked WR beats an RB
        rbs = [p for p in self.players if p.pos == "RB"][:3]
        avail = [p for p in self.players if p not in rbs]
        recs, _ = recommend(avail, rbs, self.lg, strategies.get("bpa"), 44, 53)
        self.assertNotEqual(recs[0].player.pos, "RB")

    def test_zero_rb_pushes_rb_down(self):
        bpa, _ = recommend(self.players, [], self.lg, strategies.get("bpa"), 1, 24)
        zrb, _ = recommend(self.players, [], self.lg, strategies.get("zero_rb"), 1, 24)
        self.assertEqual(bpa[0].player.pos, "RB")
        self.assertNotEqual(zrb[0].player.pos, "RB")

    def test_outlook_dropoff_positive_and_bounded(self):
        o = position_outlook(self.players, "RB", 5, 20)
        self.assertGreater(o.dropoff, 0)
        self.assertLessEqual(o.exp_value_next, base_value(o.best_now.rank))
        self.assertGreater(o.n_expected_gone, 3)

    def test_roster_slots(self):
        mine = [p for p in self.players if p.pos == "RB"][:3] + \
               [p for p in self.players if p.pos == "WR"][:1]
        rv = build_roster(mine, self.lg)
        self.assertEqual(len(rv.starters["RB"]), 2)
        self.assertEqual(len(rv.starters["FLEX"]), 1)
        self.assertEqual(rv.open_starters(self.lg)["WR"], 1)


class TestDataLoading(unittest.TestCase):
    def test_header_aliases_and_pos_rank(self):
        path = tempfile.mktemp(suffix=".csv")
        with open(path, "w") as f:
            f.write("﻿RK,Player Name,POS,Team,Tiers\n1,Bijan Robinson,RB1,ATL,1\n"
                    "2,Ja'Marr Chase,WR1,CIN,1\n3,Some Linebacker,LB,DAL,1\n4,Josh Allen,QB,BUF,2\n")
        ps = load_rankings(path)
        os.remove(path)
        self.assertEqual([p.name for p in ps], ["Bijan Robinson", "Ja'Marr Chase", "Josh Allen"])
        self.assertEqual(ps[2].rank, 3)  # densely renumbered after skipping the LB
        self.assertEqual(ps[0].pos_rank, 1)

    def test_apply_adp_initials(self):
        ps = [Player("A.J. Brown", "WR", 1), Player("Amon-Ra St. Brown", "WR", 2),
              Player("Jordan Love", "QB", 3), Player("Jeremiyah Love", "RB", 4),
              Player("Philadelphia Eagles", "DEF", 5)]
        path = tempfile.mktemp(suffix=".csv")
        with open(path, "w") as f:
            f.write("name,pos,team,adp\nA. Brown,WR,NE,20\nA. St. Brown,WR,DET,8\n"
                    "J. Love,QB,GB,95\nJ. Love,RB,ARI,40\nEagles,DEF,PHI,120\n")
        n = apply_adp(ps, path)
        os.remove(path)
        self.assertEqual(n, 5)
        self.assertEqual([p.adp for p in ps], [20, 8, 95, 40, 120])

    def test_apply_adp(self):
        ps = [Player("Bijan Robinson", "RB", 1), Player("Josh Allen", "QB", 2)]
        path = tempfile.mktemp(suffix=".csv")
        with open(path, "w") as f:
            f.write("name,adp\nbijan robinson,2.4\nJOSH ALLEN,15\nNobody,3\n")
        n = apply_adp(ps, path)
        os.remove(path)
        self.assertEqual(n, 2)
        self.assertEqual(ps[1].adp, 15.0)


class TestStateAndCli(unittest.TestCase):
    def setUp(self):
        self.players = _players()
        self.lg = League()
        self.path = tempfile.mktemp(suffix=".json")
        self.state = DraftState(slot=5, strategy="balanced", path=self.path)
        self.draft = Draft(self.players, self.lg, self.state)

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_pick_assigns_to_clock_and_persists(self):
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(4):
                self.draft.pick(self.draft.available()[0].name, mine=None)
            self.assertTrue(self.state.is_my_pick(self.lg))
            self.draft.pick(self.draft.available()[0].name, mine=None)
        self.assertEqual(len(self.state.my_keys()), 1)
        self.assertEqual(self.state.next_my_pick(self.lg), 20)
        reloaded = DraftState.load(self.path)
        self.assertEqual(len(reloaded.picks), 5)
        self.assertEqual(reloaded.picks[-1].mine, True)
        self.assertIsNotNone(self.state.undo())
        self.assertEqual(len(self.state.my_keys()), 0)

    def test_find_partial_and_pos_filter(self):
        hits = self.draft.find("rb1 sam")
        self.assertEqual(hits[0].name, "RB1 Sample")
        hits = self.draft.find("sample wr")
        self.assertTrue(all(p.pos == "WR" for p in hits))
        self.assertEqual(self.draft.find("zzzz"), [])

    def test_find_typo(self):
        ps = [Player("Amon-Ra St. Brown", "WR", 1), Player("Bijan Robinson", "RB", 2)]
        d = Draft(ps, self.lg, self.state)
        self.assertEqual(d.find("bijon robinson")[0].name, "Bijan Robinson")
        self.assertEqual(d.find("st brown")[0].pos, "WR")

    def test_lopsided_match_auto_resolves(self):
        import io, contextlib
        ps = [Player("Derrick Henry", "RB", 24), Player("Hunter Henry", "TE", 200),
              Player("Kyren Williams", "RB", 43), Player("Javonte Williams", "RB", 36)]
        d = Draft(ps, self.lg, self.state)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(d.pick("henry", mine=None))
        self.assertEqual(self.state.picks[-1].name, "Derrick Henry")
        with contextlib.redirect_stdout(io.StringIO()):
            # a real tie still asks; with no stdin it cancels
            self.assertFalse(d.pick("williams", mine=None))

    def test_find_prefers_unique_last_name(self):
        ps = [Player("Ja'Marr Chase", "WR", 3), Player("Chase Brown", "RB", 12),
              Player("A.J. Brown", "WR", 19)]
        d = Draft(ps, self.lg, self.state)
        self.assertEqual([p.name for p in d.find("chase")], ["Ja'Marr Chase"])
        self.assertEqual(len(d.find("brown")), 2)

    def test_full_draft_runs(self):
        """Auto-draft all 180 picks with the engine and check the roster is legal."""
        import io, contextlib
        for _ in range(self.lg.total_picks):
            avail = self.draft.available()
            now = self.state.current_pick
            slot = self.lg.slot_of(now)
            # everyone else drafts by ADP with a little noise; I follow the engine
            if slot == self.state.slot:
                nxt = self.state.next_my_pick(self.lg)
                recs, _ = recommend(avail, self.draft.my_players(), self.lg,
                                    self.draft.strategy, now, nxt, 1)
                choice = recs[0].player
            else:
                choice = sorted(avail, key=lambda p: p.adp_or_rank + (now * 7 % 5))[0]
                if choice.pos in ("K", "DEF") and self.lg.round_of(now) < 13:
                    choice = [p for p in avail if p.pos not in ("K", "DEF")][0]
            with contextlib.redirect_stdout(io.StringIO()):
                self.draft.pick(choice.name, mine=None)
        self.assertEqual(self.state.current_pick, self.lg.total_picks + 1)
        rv = build_roster(self.draft.my_players(), self.lg)
        self.assertEqual(rv.open_starters(self.lg), {s: 0 for s in self.lg.roster if s != "BN"})
        self.assertEqual(rv.counts["K"], 1)
        self.assertEqual(rv.counts["DEF"], 1)
        self.assertLessEqual(rv.counts["QB"], 2)
        with contextlib.redirect_stdout(io.StringIO()):
            self.draft.show()  # must not crash on a finished draft


class TestSleeper(unittest.TestCase):
    def test_parse(self):
        raw = [
            {"pick_no": 2, "draft_slot": 2, "metadata": {"first_name": "Ja'Marr", "last_name": "Chase", "position": "WR"}},
            {"pick_no": 1, "draft_slot": 1, "metadata": {"first_name": "Bijan", "last_name": "Robinson", "position": "RB"}},
            {"pick_no": 3, "draft_slot": 3, "metadata": {"first_name": "Philadelphia", "last_name": "Eagles", "position": "DEF"}},
        ]
        picks = parse_picks(raw)
        self.assertEqual([p["pick_no"] for p in picks], [1, 2, 3])
        self.assertEqual(picks[1]["key"], "jamarr chase")
        self.assertEqual(picks[2]["pos"], "DEF")
        self.assertEqual(slot_for_user({"draft_order": {"u1": 7}}, "u1"), 7)
        self.assertIsNone(slot_for_user({}, "u1"))


if __name__ == "__main__":
    unittest.main()
