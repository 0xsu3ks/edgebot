#!/usr/bin/env python3
"""EdgeBot CLI — generate data dossiers for upcoming NFL games.

Usage:
  python run.py list [--season 2026] [--week 9]     # list upcoming games w/ lines
  python run.py game <game_id>                        # dossier for one game
  python run.py slate --season 2026 --week 9 [--out reports/]  # dossier for whole slate
"""
from __future__ import annotations

import argparse
import os
import sys

from src import data as D
from src import dossier as DOS


def cmd_list(args):
    df = D.load_games(force_refresh=args.refresh)
    g = D.upcoming_games(df, season=args.season, week=args.week)
    if g.empty:
        print("No upcoming games with posted lines for that filter.")
        return
    for _, r in g.iterrows():
        sl = r["spread_line"]
        fav = r["home_team"] if sl > 0 else (r["away_team"] if sl < 0 else "PK")
        print(f"{r['game_id']:<22} {r['weekday']} {r['gameday']} {r['gametime']}  "
              f"{r['away_team']}@{r['home_team']:<4}  {fav} -{abs(sl):g}  O/U {r['total_line']}")


def cmd_game(args):
    df = D.load_games(force_refresh=args.refresh)
    text = DOS.build_dossier(df, args.game_id)
    _emit(text, args.out, args.game_id)


def cmd_slate(args):
    df = D.load_games(force_refresh=args.refresh)
    g = D.upcoming_games(df, season=args.season, week=args.week)
    if g.empty:
        print("No games for that slate.")
        return
    for _, r in g.iterrows():
        text = DOS.build_dossier(df, r["game_id"])
        _emit(text, args.out, r["game_id"])


def _emit(text: str, out: str | None, game_id: str):
    if out:
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, f"{game_id}.md")
        with open(path, "w") as f:
            f.write(text)
        print(f"wrote {path}")
    else:
        print(text)


def main():
    p = argparse.ArgumentParser(prog="edgebot")
    p.add_argument("--refresh", action="store_true", help="force refetch of nflverse data")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list"); pl.add_argument("--season", type=int); pl.add_argument("--week", type=int)
    pg = sub.add_parser("game"); pg.add_argument("game_id"); pg.add_argument("--out")
    ps = sub.add_parser("slate"); ps.add_argument("--season", type=int, required=True)
    ps.add_argument("--week", type=int, required=True); ps.add_argument("--out")

    args = p.parse_args()
    {"list": cmd_list, "game": cmd_game, "slate": cmd_slate}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
