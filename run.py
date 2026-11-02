#!/usr/bin/env python3
"""EdgeBot CLI — generate data dossiers for upcoming NFL games.

Usage:
  python run.py list [--season 2026] [--week 9]     # list upcoming games w/ lines
  python run.py game <game_id>                        # dossier for one game
  python run.py slate --season 2026 --week 9 [--out reports/]  # dossier for whole slate
"""
from __future__ import annotations

import argparse
import datetime as dt
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


def cmd_brief(args):
    """Scheduled-run entrypoint: pick unplayed games on a date window, build a
    dossier for each. Auto-detects the current season. Date defaults to today
    (America/New_York) so an 8am-ET run targets that day's games."""
    df = D.load_games(force_refresh=True)  # scheduled runs always want fresh data
    # current season = latest season that still has unplayed games
    unplayed_all = df[df["away_score"].isna()]
    season = args.season or int(unplayed_all["season"].max())

    try:
        base = (dt.date.fromisoformat(args.date) if args.date
                else dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).date())
    except ValueError:
        print(f"bad --date: {args.date}"); return
    end = base + dt.timedelta(days=args.days)

    g = D.upcoming_games(df, season=season)
    g = g[(g["gameday"] >= base.isoformat()) & (g["gameday"] <= end.isoformat())]
    if g.empty:
        print(f"No NFL games with posted lines {base}..{end} (season {season}).")
        return
    print(f"EdgeBot brief — {len(g)} game(s), {base}..{end}, season {season}\n")
    for _, r in g.iterrows():
        text = DOS.build_dossier(df, r["game_id"])
        _emit(text, args.out, r["game_id"])
        if args.out:
            print(f"  - {r['away_team']}@{r['home_team']}  ({r['gameday']})")


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
    pb = sub.add_parser("brief"); pb.add_argument("--date", help="YYYY-MM-DD (default: today ET)")
    pb.add_argument("--days", type=int, default=0, help="include this many days after --date")
    pb.add_argument("--season", type=int); pb.add_argument("--out", default="reports/")

    args = p.parse_args()
    {"list": cmd_list, "game": cmd_game, "slate": cmd_slate, "brief": cmd_brief}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
