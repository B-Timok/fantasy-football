# Fantasy football draft assistant

A command-line helper for a live snake draft. You feed it **your** rankings,
type in each pick as it happens, and it tells you who to take next and why.
No installs: Python 3.10+ standard library only.

```
python draft.py --sample --slot 5      # demo with fake players
python draft.py --slot 5               # real draft with data/rankings.csv
```

## 1. Put your rankings in `data/rankings.csv`

Any CSV with a **name** and **position** column works. Headers are matched
loosely (`Rank/RK/Overall`, `Player/Name`, `Pos/Position`, `Team`, `Tier`,
`ADP`, `Bye`, `Pos Rank`). Positions like `RB12` are fine. Rank defaults to row
order, so a two-column `name,pos` file is enough.

```
rank,name,pos,team,tier,adp,bye
1,Bijan Robinson,RB,ATL,1,1.5,5
2,Ja'Marr Chase,WR,CIN,1,2.1,10
```

Have your rankings as a pasted text list instead? Convert it:

```
python import_rankings.py my_rankings.txt            # -> data/rankings.csv
python import_rankings.py --pos RB rb.txt -o data/rankings_RB.csv
```

It understands lines like `1. Ja'Marr Chase WR CIN`, `Bijan Robinson (RB - ATL)`,
`Saquon Barkley RB1 PHI bye 9`. A line starting with `Tier N` or a blank line
starts a new tier.

Optional extras, all in `data/`:

- `rankings_QB.csv`, `rankings_RB.csv`, ... : per-position lists. They
  override the positional rank and tier from the overall file. Players only in
  a positional file get appended to the end of the overall list.
- `adp.csv` (`name,adp`): average draft position. Build it with
  `python fetch_adp.py --text pasted.txt [--col N]` from any copied ADP table.
  This is what makes the "will he be there next time" numbers good. Without it
  the tool assumes everyone drafts by your rankings.
- `adp_overrides.csv`: manual ADP for players the site doesn't list or where
  you disagree; applied after `adp.csv` and never overwritten.
- `league.json`: teams, roster slots, rounds. Already set for 12-team PPR,
  1 QB / 2 RB / 2 WR / TE / FLEX / K / DEF / 6 bench.

**Tiers matter.** The tool gives a bonus to the last player in a tier at his
position, so mark tier breaks if you have opinions about them.

## 2. Run it during the draft

```
python draft.py --slot 7
```

Every pick, type the player's name (a partial last name is enough) and hit
enter. The pick is assigned to whichever team is on the clock, so you almost
never need to say whose pick it was. If the name is ambiguous you get a
numbered list.

```
> chase            # team on the clock took Ja'Marr Chase
> bijan            # next team took Bijan Robinson
> me lamb          # force-assign to you (rarely needed)
> undo             # oops
> b rb             # best available RBs
> b 30             # best available, top 30
> strategy         # list strategies; "strategy zero_rb" switches
> r                # your roster
> taken            # last picks
> find brown       # search, including taken players
> next             # your remaining pick numbers
```

State is saved to `data/draft_state.json` after every pick. If the terminal
dies, run the same command again and it resumes. `--new` starts over.

Sleeper users: put the draft id (the number in the draft URL) in
`league.json` as `sleeper_draft_id`, then type `sync` to pull picks instead of
typing them. Both ways can be mixed.

## Practice first: mock mode

```
python draft.py --mock --slot 7
```

The other eleven teams draft automatically by ADP with a little randomness
(respecting sane roster limits, K/DEF late). You only make your own picks;
type `auto` to let the engine pick for you. `--seed 3` makes a mock
repeatable. Mock state is kept separately (`data/mock_state.json`) so it
never touches a real draft in progress.

## Simulate: which strategy for which slot, and tuning

```
python simulate.py                 # every slot x every strategy, 60 drafts each
python simulate.py --sims 200 --slots 7
python simulate.py --tune          # random search over the engine weights
```

Opponents draft by ADP with noise; you follow the engine. Each roster is
scored by the estimated points per game of its best starting lineup plus
0.3 x the top three bench RB/WR. Results go to `data/sim_results.json`,
which `draft.py` reads to suggest a strategy for your slot, and `--tune`
writes `data/engine_params.json`, which `draft.py` loads automatically.
Caveats: the scorer uses last season's points per game (rookies get a
positional estimate), and opponents never deviate from ADP by strategy.

## 3. How it decides

For every available player:

```
score = value × strategy × need  +  0.8 × urgency × strategy × need  +  tier bonus
```

- **value**: your overall rank, mapped to a 0-100 curve that is steep at the
  top (the gap between rank 1 and 12 is much bigger than 100 to 111).
- **need**: 1.0 if the player fills an open starting slot, 0.92 for FLEX,
  then a bench discount (RB/WR 0.8, QB/TE 0.45). When your remaining picks
  equal your open starting slots, only players that fill one keep full value.
  K and DEF are suppressed until the last two rounds no matter what.
- **urgency**: the important bit. Using ADP and the number of picks until
  you're up again, it estimates the best player you could expect at each
  position at your *next* pick. Urgency is how much better this player is
  than that. A big number means "take him now or lose the tier"; a small one
  means you can wait on that position.
- **strategy**: per-position multipliers by round. `strategy` lists them:
  `bpa`, `balanced`, `hero_rb`, `zero_rb`, `robust_rb`, `elite_onesies`. One
  is suggested from your slot; switch any time, e.g. when an RB run happens
  before your pick and `zero_rb` starts making more sense.

The **position outlook** table under the recommendations shows, for each
position, the best player now, who you'd likely get at your next pick, the
value drop between them, and how many players at that position are expected
to go before you pick again. That table is the quickest way to answer "can I
wait on TE?".

All the knobs (weights, discounts, strategy multipliers) are constants at the
top of `draftkit/engine.py` and `draftkit/strategies.py`.

## Tests

```
python -m unittest discover -s tests
```
