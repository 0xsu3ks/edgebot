#!/usr/bin/env python3
"""EdgeBot — self-contained NFL betting-data dossier generator.

Single-file build of src/{data,angles,dossier}.py + the brief runner, so it can
be embedded in a scheduled-task prompt and run with no repo checkout.
Data source: nflverse master games.csv via raw.githubusercontent.com (no key).
No wagers placed or recommended — data points only.

Usage:
  python3 edgebot_standalone.py --days 0 --out reports/      # today's NFL games
  python3 edgebot_standalone.py --date 2026-11-08 --out reports/
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import sys

import pandas as pd

RAW_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"

STR_COLS = ("referee", "away_qb_name", "home_qb_name", "away_coach", "home_coach")


# ---------- data ----------------------------------------------------------

def load_games() -> pd.DataFrame:
    df = pd.read_csv(RAW_URL)
    for c in STR_COLS:
        if c in df.columns:
            df[c] = df[c].astype("string").str.strip()
    return df


def upcoming_games(df, season=None, week=None, with_lines=True):
    g = df[df["away_score"].isna()].copy()
    if season is not None:
        g = g[g["season"] == season]
    if week is not None:
        g = g[g["week"] == week]
    if with_lines:
        g = g[g["spread_line"].notna()]
    return g.sort_values("gameday")


def played(df):
    return df[df["result"].notna()].copy()


# ---------- helpers -------------------------------------------------------

def _implied(ml):
    if ml is None or (isinstance(ml, float) and math.isnan(ml)):
        return None
    ml = float(ml)
    return round(-ml / (-ml + 100) * 100, 1) if ml < 0 else round(100 / (ml + 100) * 100, 1)


def _pct(n, d):
    return None if d == 0 else round(100 * n / d, 1)


def _rest_label(days):
    if pd.isna(days):
        return "unknown"
    days = int(days)
    if days >= 13:
        return f"off bye ({days}d)"
    if days <= 5:
        return f"short week ({days}d)"
    if days >= 8:
        return f"extra rest ({days}d)"
    return f"normal ({days}d)"


def _rest_bucket(days):
    """(key, label) for a rest-day count; buckets match qb_splits filters."""
    if pd.isna(days):
        return None, "unknown rest"
    days = int(days)
    if days >= 13:
        return "off_bye", f"off a bye ({days}d)"
    if days <= 5:
        return "short", f"short week ({days}d)"
    if days >= 8:
        return "extra", f"extra rest ({days}d)"
    return "normal", f"normal rest ({days}d)"


def _bucket_frame(q, key):
    if key == "off_bye":
        return q[q["rest"] >= 13]
    if key == "short":
        return q[q["rest"] <= 5]
    if key == "extra":
        return q[(q["rest"] >= 8) & (q["rest"] <= 12)]
    if key == "normal":
        return q[(q["rest"] >= 6) & (q["rest"] <= 7)]
    return q.iloc[0:0]


def _qb_frame(pl, qb):
    home = pl[pl["home_qb_name"] == qb].copy()
    away = pl[pl["away_qb_name"] == qb].copy()
    home["is_home"] = True
    home["won"] = home["result"] > 0
    home["ats_margin"] = home["result"] - home["spread_line"]
    home["rest"] = home["home_rest"]
    away["is_home"] = False
    away["won"] = away["result"] < 0
    away["ats_margin"] = -(away["result"] - away["spread_line"])
    away["rest"] = away["away_rest"]
    q = pd.concat([home, away], ignore_index=True)
    if q.empty:
        return q
    q["covered"] = q["ats_margin"].apply(lambda m: None if abs(m) < 1e-9 else (m > 0))
    q["went_over"] = q["total"] > q["total_line"]
    return q.sort_values("gameday")


def _rec(frame):
    n = len(frame)
    if n == 0:
        return {"games": 0}
    su_w = int(frame["won"].sum())
    cov = frame["covered"].dropna()
    aw = int((cov == True).sum())   # noqa: E712
    al = int((cov == False).sum())  # noqa: E712
    pu = int(frame["covered"].isna().sum())
    ov = frame["went_over"].dropna()
    return {
        "games": n,
        "su": f"{su_w}-{n - su_w}", "su_pct": _pct(su_w, n),
        "ats": f"{aw}-{al}" + (f"-{pu}" if pu else ""), "ats_pct": _pct(aw, aw + al),
        "over": f"{int(ov.sum())}-{int((~ov).sum())}",
        "over_pct": _pct(int(ov.sum()), len(ov)) if len(ov) else None,
    }


# ---------- angles --------------------------------------------------------

def line_summary(game):
    sl = game["spread_line"]
    home, away = game["home_team"], game["away_team"]
    if pd.isna(sl):
        line = None
    elif sl > 0:
        line = f"{home} -{abs(sl):g}"
    elif sl < 0:
        line = f"{away} -{abs(sl):g}"
    else:
        line = "PK"
    return {
        "matchup": f"{away} @ {home}", "spread": line,
        "total": None if pd.isna(game["total_line"]) else float(game["total_line"]),
        "home_ml": None if pd.isna(game["home_moneyline"]) else int(game["home_moneyline"]),
        "away_ml": None if pd.isna(game["away_moneyline"]) else int(game["away_moneyline"]),
        "home_imp": _implied(game["home_moneyline"]), "away_imp": _implied(game["away_moneyline"]),
        "roof": game.get("roof"), "surface": game.get("surface"), "stadium": game.get("stadium"),
    }


def rest_edge(game):
    ar, hr = game["away_rest"], game["home_rest"]
    diff = (hr - ar) if (not pd.isna(hr) and not pd.isna(ar)) else None
    note = None
    if diff is not None:
        if diff >= 3:
            note = f"{game['home_team']} fresher (+{int(diff)}d)"
        elif diff <= -3:
            note = f"{game['away_team']} fresher (+{int(-diff)}d)"
        else:
            note = "no material rest edge"
    return {"away": (game["away_team"], _rest_label(ar)), "home": (game["home_team"], _rest_label(hr)), "note": note}


def qb_splits(pl, qb, side, this_rest=None):
    if not isinstance(qb, str) or not qb:
        return {"qb": qb, "note": "no QB listed"}
    q = _qb_frame(pl, qb)
    if q.empty:
        return {"qb": qb, "note": "no completed starts in dataset"}
    this_side = q[q["is_home"]] if side == "home" else q[q["is_home"] == False]  # noqa: E712
    # THIS-SPOT: the QB's record in the rest situation that applies to THIS game
    bkey, blabel = _rest_bucket(this_rest)
    this_spot = _rec(_bucket_frame(q, bkey)) if bkey else {"games": 0}
    return {
        "qb": qb, "career": _rec(q), f"as_{side}": _rec(this_side),
        "this_spot": this_spot, "this_spot_label": blabel,
        "off_bye": _rec(q[q["rest"] >= 13]), "short_week": _rec(q[q["rest"] <= 5]),
        "last_10": _rec(q.tail(10)), "side": side,
    }


def head_to_head(pl, home, away, n=6):
    m = pl[((pl["home_team"] == home) & (pl["away_team"] == away)) |
           ((pl["home_team"] == away) & (pl["away_team"] == home))].sort_values("gameday").tail(n)
    games, overs, hc, dec = [], 0, 0, 0
    for _, r in m.iterrows():
        over = r["total"] > r["total_line"] if not pd.isna(r["total_line"]) else None
        if over is True:
            overs += 1
        if not pd.isna(r["spread_line"]):
            ham = r["result"] - r["spread_line"]
            cur = ham if r["home_team"] == home else -ham
            if abs(cur) > 1e-9:
                dec += 1
                hc += 1 if cur > 0 else 0
        games.append(f"{r['gameday']}: {r['away_team']} {int(r['away_score'])} @ "
                     f"{r['home_team']} {int(r['home_score'])} ({int(r['total']) if not pd.isna(r['total']) else '?'} pts)")
    return {"games": games, "n": len(games),
            "over_rate": _pct(overs, len([g for g in m.iterrows() if not pd.isna(g[1]['total_line'])])),
            "home_ats": f"{hc}-{dec - hc}" if dec else "n/a"}


def referee_tendency(pl, ref, seasons_back=6):
    if not isinstance(ref, str) or not ref:
        return {"ref": ref, "note": "no referee assigned yet"}
    r = pl[pl["referee"] == ref]
    if not r.empty:
        r = r[r["season"] >= r["season"].max() - seasons_back + 1]
    if r.empty:
        return {"ref": ref, "games": 0, "note": "no games under this ref in window"}
    ov = (r["total"] > r["total_line"]).dropna()
    ham = r["result"] - r["spread_line"]
    hc = int((ham > 0).sum())
    dec = int((ham.abs() > 1e-9).sum())
    return {"ref": ref, "games": int(len(r)), "over_rate": _pct(int(ov.sum()), len(ov)),
            "avg_total": round(r["total"].mean(), 1), "home_ats_pct": _pct(hc, dec)}


# ---------- dossier -------------------------------------------------------

def _recline(label, rec):
    if not rec or rec.get("games", 0) == 0:
        return f"- **{label}:** no games"
    bits = [f"{rec['games']}g"]
    if rec.get("su"):
        bits.append(f"SU {rec['su']} ({rec['su_pct']}%)")
    if rec.get("ats"):
        atsp = f" ({rec['ats_pct']}%)" if rec.get("ats_pct") is not None else ""
        bits.append(f"ATS {rec['ats']}{atsp}")
    if rec.get("over_pct") is not None:
        bits.append(f"O/U {rec['over']} (O {rec['over_pct']}%)")
    return f"- **{label}:** " + " · ".join(bits)


def build_dossier(df, game):
    pl = played(df)
    pl = pl[pl["gameday"] < game["gameday"]]  # no leakage
    ls = line_summary(game)
    rest = rest_edge(game)
    h2h = head_to_head(pl, game["home_team"], game["away_team"])
    ref = referee_tendency(pl, game.get("referee"))
    aqb = qb_splits(pl, game.get("away_qb_name"), "away", game.get("away_rest"))
    hqb = qb_splits(pl, game.get("home_qb_name"), "home", game.get("home_rest"))
    o = []
    o.append(f"# {ls['matchup']} — {game['weekday']} {game['gameday']} {game['gametime']} ET "
             f"(Wk {int(game['week'])})")
    o.append(f"**Line:** {ls['spread']} | **Total:** {ls['total']} | "
             f"ML {game['away_team']} {ls['away_ml']} ({ls['away_imp']}%) / "
             f"{game['home_team']} {ls['home_ml']} ({ls['home_imp']}%)")
    o.append(f"**Venue:** {ls['stadium']} · {ls['roof']} · {ls['surface']}")
    o.append(f"**Rest:** {rest['away'][0]} {rest['away'][1]} | {rest['home'][0]} {rest['home'][1]} → {rest['note']}")
    o.append(f"\n**QB {game['away_team']} — {aqb.get('qb')}**")
    if aqb.get("note"):
        o.append(f"- _{aqb['note']}_")
    else:
        o.append(_recline(f"THIS SPOT ({aqb['this_spot_label']})", aqb["this_spot"]))
        o.append("- _career reference splits (not tonight's spot):_")
        o += [_recline("  Career", aqb["career"]), _recline("  As road QB", aqb["as_away"]),
              _recline("  Off a bye", aqb["off_bye"]), _recline("  Short week", aqb["short_week"]),
              _recline("  Last 10", aqb["last_10"])]
    o.append(f"\n**QB {game['home_team']} — {hqb.get('qb')}**")
    if hqb.get("note"):
        o.append(f"- _{hqb['note']}_")
    else:
        o.append(_recline(f"THIS SPOT ({hqb['this_spot_label']})", hqb["this_spot"]))
        o.append("- _career reference splits (not tonight's spot):_")
        o += [_recline("  Career", hqb["career"]), _recline("  As home QB", hqb["as_home"]),
              _recline("  Off a bye", hqb["off_bye"]), _recline("  Short week", hqb["short_week"]),
              _recline("  Last 10", hqb["last_10"])]
    o.append("\n**Head-to-head**")
    if h2h["n"] == 0:
        o.append("- none in dataset")
    else:
        o.append(f"- last {h2h['n']}: {game['home_team']} ATS {h2h['home_ats']}, over-rate {h2h['over_rate']}%")
        o += [f"  - {g}" for g in h2h["games"]]
    o.append("\n**Referee**")
    o.append(f"- {ref.get('ref')}: " + (ref["note"] if ref.get("games", 0) == 0 else
             f"{ref['games']}g, over {ref['over_rate']}%, avg {ref['avg_total']} pts, home ATS {ref['home_ats_pct']}%"))
    roof = str(ls["roof"]).lower()
    if roof in ("dome", "closed", "indoor", "retractable"):
        o.append("\n**Weather:** indoor — n/a")
    else:
        o.append("\n**Weather:** OUTDOOR — live game-time forecast NOT wired (needs weather API host allowlisted)")
    o.append("\n_Data: nflverse. No wager placed or recommended — data points only._")
    return "\n".join(o)


# ---------- runner --------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="YYYY-MM-DD (default today ET)")
    p.add_argument("--days", type=int, default=0)
    p.add_argument("--season", type=int)
    p.add_argument("--out", default="reports/")
    a = p.parse_args()

    df = load_games()
    season = a.season or int(df[df["away_score"].isna()]["season"].max())
    try:
        base = (dt.date.fromisoformat(a.date) if a.date
                else dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).date())
    except ValueError:
        print(f"bad --date: {a.date}"); return 1
    end = base + dt.timedelta(days=a.days)

    g = upcoming_games(df, season=season)
    g = g[(g["gameday"] >= base.isoformat()) & (g["gameday"] <= end.isoformat())]
    if g.empty:
        print(f"NO_GAMES: no NFL games with posted lines {base}..{end} (season {season}).")
        return 0

    os.makedirs(a.out, exist_ok=True)
    print(f"EdgeBot brief — {len(g)} game(s), {base}..{end}, season {season}\n")
    for _, r in g.iterrows():
        text = build_dossier(df, r)
        path = os.path.join(a.out, f"{r['game_id']}.md")
        with open(path, "w") as f:
            f.write(text)
        print(f"wrote {path}  ({r['away_team']}@{r['home_team']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
