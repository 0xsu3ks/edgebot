"""Analysis layer: turn raw game rows into betting 'angles' (data points).

Every function returns plain dicts/lists of numbers and short strings so the
analysis is auditable and the LLM (or a human) reads facts, never guesses them.

Key conventions (verified against nflverse data):
  spread_line  > 0  => HOME favored by that many points
  result        = home_score - away_score
  home ATS margin = result - spread_line   (>0 home covers, <0 away covers, 0 push)
  total (actual)  vs total_line (bet)      => over if total > total_line
  rest: 7 normal, >=13 off a bye, <=5 short week (e.g. Thu after Sun)
"""
from __future__ import annotations

import math
import pandas as pd


# ---------- small helpers -------------------------------------------------

def _american_to_implied(ml) -> float | None:
    if ml is None or (isinstance(ml, float) and math.isnan(ml)):
        return None
    ml = float(ml)
    if ml < 0:
        return round(-ml / (-ml + 100) * 100, 1)
    return round(100 / (ml + 100) * 100, 1)


def _pct(n, d):
    return None if d == 0 else round(100 * n / d, 1)


def _rest_label(days) -> str:
    if pd.isna(days):
        return "unknown"
    days = int(days)
    if days >= 13:
        return f"off bye ({days}d)"
    if days <= 5:
        return f"short week ({days}d)"
    return f"normal ({days}d)"


def _qb_game_frame(played: pd.DataFrame, qb_name: str) -> pd.DataFrame:
    """All completed games a QB started, normalized to that QB's perspective.

    Adds columns: is_home, won (SU), ats_margin (QB-team perspective, >0=cover),
    covered (bool/na for push), total_pts, went_over, rest, opp.
    """
    home = played[played["home_qb_name"] == qb_name].copy()
    away = played[played["away_qb_name"] == qb_name].copy()

    home["is_home"] = True
    home["won"] = home["result"] > 0
    home["ats_margin"] = home["result"] - home["spread_line"]
    home["rest"] = home["home_rest"]
    home["opp"] = home["away_team"]

    away["is_home"] = False
    away["won"] = away["result"] < 0
    # away ATS margin is the negation of home ATS margin
    away["ats_margin"] = -(away["result"] - away["spread_line"])
    away["rest"] = away["away_rest"]
    away["opp"] = away["home_team"]

    q = pd.concat([home, away], ignore_index=True)
    if q.empty:
        return q
    q["covered"] = q["ats_margin"].apply(
        lambda m: None if abs(m) < 1e-9 else (m > 0))
    q["total_pts"] = q["total"]
    q["went_over"] = q["total"] > q["total_line"]
    return q.sort_values("gameday")


def _split_record(frame: pd.DataFrame) -> dict:
    """Straight-up + ATS + O/U record for a set of a QB's games."""
    n = len(frame)
    if n == 0:
        return {"games": 0}
    su_w = int(frame["won"].sum())
    cov = frame["covered"].dropna()
    ats_w = int((cov == True).sum())   # noqa: E712
    ats_l = int((cov == False).sum())  # noqa: E712
    pushes = int(frame["covered"].isna().sum())
    ov = frame["went_over"].dropna()
    return {
        "games": n,
        "su": f"{su_w}-{n - su_w}",
        "su_pct": _pct(su_w, n),
        "ats": f"{ats_w}-{ats_l}" + (f"-{pushes}" if pushes else ""),
        "ats_pct": _pct(ats_w, ats_w + ats_l),
        "over": f"{int(ov.sum())}-{int((~ov).sum())}",
        "over_pct": _pct(int(ov.sum()), len(ov)) if len(ov) else None,
        "avg_margin": round(frame["result"].abs().mean(), 1),
    }


# ---------- public angle builders ----------------------------------------

def line_summary(game: pd.Series) -> dict:
    sl = game["spread_line"]
    home, away = game["home_team"], game["away_team"]
    if pd.isna(sl):
        fav, line = None, None
    elif sl > 0:
        fav, line = home, f"{home} -{abs(sl):g}"
    elif sl < 0:
        fav, line = away, f"{away} -{abs(sl):g}"
    else:
        fav, line = "pick'em", "PK"
    return {
        "matchup": f"{away} @ {home}",
        "favorite": fav,
        "spread": line,
        "total": None if pd.isna(game["total_line"]) else float(game["total_line"]),
        "home_ml": None if pd.isna(game["home_moneyline"]) else int(game["home_moneyline"]),
        "away_ml": None if pd.isna(game["away_moneyline"]) else int(game["away_moneyline"]),
        "home_implied_win_pct": _american_to_implied(game["home_moneyline"]),
        "away_implied_win_pct": _american_to_implied(game["away_moneyline"]),
        "roof": game.get("roof"),
        "surface": game.get("surface"),
        "stadium": game.get("stadium"),
    }


def rest_edge(game: pd.Series) -> dict:
    ar, hr = game["away_rest"], game["home_rest"]
    diff = (hr - ar) if (not pd.isna(hr) and not pd.isna(ar)) else None
    note = None
    if diff is not None:
        if diff >= 3:
            note = f"{game['home_team']} meaningfully fresher (+{int(diff)}d)"
        elif diff <= -3:
            note = f"{game['away_team']} meaningfully fresher (+{int(-diff)}d)"
        else:
            note = "no material rest edge"
    return {
        "away": {"team": game["away_team"], "rest": _rest_label(ar)},
        "home": {"team": game["home_team"], "rest": _rest_label(hr)},
        "rest_diff_home_minus_away": None if diff is None else int(diff),
        "note": note,
    }


def qb_splits(played: pd.DataFrame, qb_name: str, side: str,
              seasons_back: int | None = None) -> dict:
    """Situational splits for one QB. side is 'home' or 'away' for THIS game."""
    if not isinstance(qb_name, str) or not qb_name:
        return {"qb": qb_name, "note": "no QB listed"}
    q = _qb_game_frame(played, qb_name)
    if q.empty:
        return {"qb": qb_name, "note": "no completed starts in dataset"}
    if seasons_back:
        cutoff = q["season"].max() - seasons_back + 1
        q = q[q["season"] >= cutoff]

    this_side = q[q["is_home"]] if side == "home" else q[q["is_home"] == False]  # noqa: E712
    after_bye = q[q["rest"] >= 13]
    short_wk = q[q["rest"] <= 5]
    last10 = q.tail(10)

    return {
        "qb": qb_name,
        "career_in_data": _split_record(q),
        f"as_{side}": _split_record(this_side),
        "off_bye": _split_record(after_bye),
        "short_week": _split_record(short_wk),
        "last_10": _split_record(last10),
    }


def head_to_head(played: pd.DataFrame, home: str, away: str, n: int = 6) -> dict:
    m = played[((played["home_team"] == home) & (played["away_team"] == away)) |
               ((played["home_team"] == away) & (played["away_team"] == home))]
    m = m.sort_values("gameday").tail(n)
    games = []
    overs = 0
    home_team_ats = 0
    ats_decided = 0
    for _, r in m.iterrows():
        over = r["total"] > r["total_line"] if not pd.isna(r["total_line"]) else None
        if over is True:
            overs += 1
        # did the *current home team* cover in that historical game?
        if not pd.isna(r["spread_line"]):
            home_ats_margin = r["result"] - r["spread_line"]
            cur_home_margin = home_ats_margin if r["home_team"] == home else -home_ats_margin
            if abs(cur_home_margin) > 1e-9:
                ats_decided += 1
                if cur_home_margin > 0:
                    home_team_ats += 1
        games.append({
            "date": r["gameday"], "season": int(r["season"]),
            "score": f"{r['away_team']} {int(r['away_score'])} @ {r['home_team']} {int(r['home_score'])}",
            "total_pts": None if pd.isna(r["total"]) else int(r["total"]),
            "total_line": None if pd.isna(r["total_line"]) else float(r["total_line"]),
        })
    return {
        "meetings": games,
        "n": len(games),
        "over_rate_pct": _pct(overs, len([g for g in games if g["total_line"] is not None])),
        f"{home}_ats_in_series": f"{home_team_ats}-{ats_decided - home_team_ats}" if ats_decided else "n/a",
    }


def referee_tendency(played: pd.DataFrame, referee, seasons_back: int = 6) -> dict:
    if not isinstance(referee, str) or not referee:
        return {"referee": referee, "note": "no referee assigned yet"}
    r = played[played["referee"] == referee]
    if not r.empty:
        r = r[r["season"] >= r["season"].max() - seasons_back + 1]
    if r.empty:
        return {"referee": referee, "games": 0,
                "note": "no completed games under this ref in window"}
    ov = (r["total"] > r["total_line"]).dropna()
    home_ats = (r["result"] - r["spread_line"])
    home_cov = int((home_ats > 0).sum())
    home_decided = int((home_ats.abs() > 1e-9).sum())
    return {
        "referee": referee,
        "games": int(len(r)),
        "over_rate_pct": _pct(int(ov.sum()), len(ov)),
        "avg_total_pts": round(r["total"].mean(), 1),
        "avg_penalty_note": "penalty counts not in this dataset (add via nflverse PBP)",
        "home_ats_pct": _pct(home_cov, home_decided),
    }


def venue_weather(game: pd.Series, played: pd.DataFrame) -> dict:
    """What we can say about conditions from the schedule data, plus an explicit
    flag that a LIVE game-time forecast requires an allowlisted weather API."""
    roof = game.get("roof")
    indoor = str(roof).lower() in ("dome", "closed", "indoor", "retractable")
    stadium = game.get("stadium")
    hist = played[(played["stadium"] == stadium) & (played["roof"].isin(["outdoors", "open"]))]
    wind_avg = round(hist["wind"].dropna().mean(), 1) if not hist.empty else None
    temp_avg = round(hist["temp"].dropna().mean(), 1) if not hist.empty else None
    return {
        "stadium": stadium,
        "roof": roof,
        "surface": game.get("surface"),
        "weather_relevant": (not indoor),
        "historical_outdoor_avg_wind_mph": None if indoor else wind_avg,
        "historical_outdoor_avg_temp_f": None if indoor else temp_avg,
        "LIVE_FORECAST": "UNAVAILABLE — needs api.weather.gov allowlisted (see README)"
        if not indoor else "n/a (indoor)",
    }
