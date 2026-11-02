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
import collections
import datetime as dt
import math
import os
import re
import sys

import pandas as pd

try:
    import requests
except Exception:  # pragma: no cover
    requests = None
try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = None

# Home-stadium coordinates (city/stadium level is fine for a game-time read).
# Dome/closed games are skipped before this is used, so indoor coords are
# harmless if present.
NFL_STADIUM_COORDS = {
    "ARI": (33.5277, -112.2626), "ATL": (33.7554, -84.4009), "BAL": (39.2780, -76.6227),
    "BUF": (42.7738, -78.7870), "CAR": (35.2258, -80.8528), "CHI": (41.8623, -87.6167),
    "CIN": (39.0954, -84.5160), "CLE": (41.5061, -81.6995), "DAL": (32.7473, -97.0945),
    "DEN": (39.7439, -105.0201), "DET": (42.3400, -83.0456), "GB": (44.5013, -88.0622),
    "HOU": (29.6847, -95.4107), "IND": (39.7601, -86.1639), "JAX": (30.3240, -81.6373),
    "KC": (39.0489, -94.4839), "LV": (36.0909, -115.1833), "LAC": (33.9535, -118.3392),
    "LA": (33.9535, -118.3392), "MIA": (25.9580, -80.2389), "MIN": (44.9736, -93.2575),
    "NE": (42.0909, -71.2643), "NO": (29.9511, -90.0812), "NYG": (40.8135, -74.0745),
    "NYJ": (40.8135, -74.0745), "PHI": (39.9008, -75.1675), "PIT": (40.4468, -80.0158),
    "SEA": (47.5952, -122.3316), "SF": (37.4030, -121.9697), "TB": (27.9759, -82.5033),
    "TEN": (36.1665, -86.7713), "WAS": (38.9076, -76.8645),
}
_NWS_HDRS = {"User-Agent": "edgebot/1.0 (research; contact via github 0xsu3ks)",
             "Accept": "application/geo+json"}


def fetch_weather(game):
    """Game-time forecast via NWS (api.weather.gov, no key). Fail-safe: any
    problem returns a short 'unavailable' note and never raises."""
    roof = str(game.get("roof")).lower()
    if roof in ("dome", "closed", "indoor"):
        return {"relevant": False, "text": "indoor — weather n/a"}
    if requests is None:
        return {"relevant": True, "text": "forecast unavailable (requests missing)"}
    coords = NFL_STADIUM_COORDS.get(game.get("home_team"))
    if not coords:
        return {"relevant": True, "text": "forecast unavailable (no stadium coords)"}
    try:
        pt = requests.get(f"https://api.weather.gov/points/{coords[0]},{coords[1]}",
                          headers=_NWS_HDRS, timeout=12)
        if pt.status_code != 200:
            return {"relevant": True, "text": f"forecast unavailable (points {pt.status_code})"}
        hurl = pt.json()["properties"]["forecastHourly"]
        periods = requests.get(hurl, headers=_NWS_HDRS, timeout=12).json()["properties"]["periods"]
        # kickoff as an aware datetime in ET, then nearest hourly period
        kick = dt.datetime.fromisoformat(f"{game['gameday']}T{game['gametime']}:00")
        if _ET is not None:
            kick = kick.replace(tzinfo=_ET)
        best, bestdiff = None, None
        for p in periods:
            try:
                ts = dt.datetime.fromisoformat(p["startTime"])
            except Exception:
                continue
            diff = abs((ts - kick).total_seconds()) if kick.tzinfo else None
            if diff is None:
                best = p
                break
            if bestdiff is None or diff < bestdiff:
                best, bestdiff = p, diff
        if not best:
            return {"relevant": True, "text": "forecast unavailable (no matching hour)"}
        pop = (best.get("probabilityOfPrecipitation") or {}).get("value")
        wnums = re.findall(r"\d+", str(best.get("windSpeed") or ""))
        wind_mph = max(int(n) for n in wnums) if wnums else None
        return {
            "relevant": True, "wind_mph": wind_mph,
            "temp": best.get("temperature"), "temp_unit": best.get("temperatureUnit", "F"),
            "wind": best.get("windSpeed"), "wind_dir": best.get("windDirection"),
            "precip_pct": pop, "sky": best.get("shortForecast"),
            "text": f"{best.get('temperature')}°{best.get('temperatureUnit','F')}, "
                    f"wind {best.get('windSpeed')} {best.get('windDirection','')}, "
                    f"{'' if pop is None else str(pop)+'% precip, '}{best.get('shortForecast','')}",
        }
    except Exception as e:  # never break the brief over weather
        return {"relevant": True, "text": f"forecast unavailable ({type(e).__name__})"}


NFL_FULLNAME = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LV": "Las Vegas Raiders", "LAC": "Los Angeles Chargers",
    "LA": "Los Angeles Rams", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
    "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks", "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}
FULLNAME_TO_ABBR = {v: k for k, v in NFL_FULLNAME.items()}


def _summ(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    return {"consensus": round(med, 1), "min": min(s), "max": max(s), "n": n}


def fetch_all_odds():
    """LIVE odds for the whole slate from The Odds API (one request). Returns a
    dict keyed by (home_abbr, away_abbr). Fail-safe: {} if no key, {'_error':..}
    on failure. ODDS_API_KEY must be set and api.the-odds-api.com allowlisted."""
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        return {"_status": "no_key"}
    if requests is None:
        return {"_error": "requests missing"}
    try:
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds",
            params={"apiKey": key, "regions": "us", "markets": "spreads,totals",
                    "oddsFormat": "american"}, timeout=20)
        if r.status_code != 200:
            return {"_error": f"HTTP {r.status_code}"}
        out = {"_fetched_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
               "_remaining": r.headers.get("x-requests-remaining")}
        for ev in r.json():
            ha = FULLNAME_TO_ABBR.get(ev.get("home_team"))
            aa = FULLNAME_TO_ABBR.get(ev.get("away_team"))
            if not ha or not aa:
                continue
            hsp, tot = [], []
            for bk in ev.get("bookmakers", []):
                for m in bk.get("markets", []):
                    if m.get("key") == "spreads":
                        for o in m.get("outcomes", []):
                            if FULLNAME_TO_ABBR.get(o.get("name")) == ha and o.get("point") is not None:
                                hsp.append(o["point"])
                    elif m.get("key") == "totals":
                        for o in m.get("outcomes", []):
                            if o.get("name") == "Over" and o.get("point") is not None:
                                tot.append(o["point"])
            out[(ha, aa)] = {"home_spread": _summ(hsp), "total": _summ(tot),
                             "n_books": len(ev.get("bookmakers", []))}
        return out
    except Exception as e:
        return {"_error": type(e).__name__}

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


TEAM_TZ = {
    "ATL": "America/New_York", "BAL": "America/New_York", "BUF": "America/New_York",
    "CAR": "America/New_York", "CIN": "America/New_York", "CLE": "America/New_York",
    "DET": "America/New_York", "IND": "America/New_York", "JAX": "America/New_York",
    "MIA": "America/New_York", "NE": "America/New_York", "NYG": "America/New_York",
    "NYJ": "America/New_York", "PHI": "America/New_York", "PIT": "America/New_York",
    "TB": "America/New_York", "WAS": "America/New_York",
    "CHI": "America/Chicago", "DAL": "America/Chicago", "GB": "America/Chicago",
    "HOU": "America/Chicago", "KC": "America/Chicago", "MIN": "America/Chicago",
    "NO": "America/Chicago", "TEN": "America/Chicago",
    "DEN": "America/Denver", "ARI": "America/Phoenix",
    "LV": "America/Los_Angeles", "LAC": "America/Los_Angeles", "LA": "America/Los_Angeles",
    "SF": "America/Los_Angeles", "SEA": "America/Los_Angeles",
}


def compute_travel(game):
    """Travel + body-clock angle for the ROAD team. Fail-safe -> None."""
    if _ET is None:
        return None
    htz, atz = TEAM_TZ.get(game["home_team"]), TEAM_TZ.get(game["away_team"])
    if not htz or not atz:
        return None
    try:
        kick = dt.datetime.fromisoformat(f"{game['gameday']}T{game['gametime']}:00").replace(tzinfo=_ET)
        local = kick.astimezone(ZoneInfo(htz))     # actual stadium local time
        body = kick.astimezone(ZoneInfo(atz))       # road team's body clock
        zones = round((local.utcoffset() - body.utcoffset()).total_seconds() / 3600)
        bh = body.hour + body.minute / 60.0
        notes = []
        if zones == 0:
            notes.append(f"{game['away_team']} in-zone (no TZ change)")
        else:
            direction = "EAST" if zones > 0 else "WEST"
            notes.append(f"{game['away_team']} travels {abs(zones)} TZ {direction}; "
                         f"body-clock kickoff ~{body.strftime('%-I:%M%p').lower()}")
            if zones >= 2 and bh < 11:
                notes.append("⚠ early body clock for road team (West→East early window — historically underperforms)")
            if kick.hour >= 20 and atz == "America/Los_Angeles":
                notes.append("West-coast road team in a night game — body clock still afternoon (slight edge)")
        return {"zones": zones, "body_hour": round(bh, 1), "notes": notes}
    except Exception:
        return None


def compute_srs(df, season, cap=24, iters=20):
    """Simple Rating System (avg scoring margin adjusted for strength of schedule),
    a power rating computed purely from this season's scores. {team: rating}."""
    gm = df[(df["season"] == season) & df["result"].notna()]
    if gm.empty:
        return {}
    opps = collections.defaultdict(list)
    for _, r in gm.iterrows():
        m = max(-cap, min(cap, float(r["result"])))  # cap blowouts
        opps[r["home_team"]].append((r["away_team"], m))
        opps[r["away_team"]].append((r["home_team"], -m))
    teams = list(opps)
    avg = {t: sum(x for _, x in opps[t]) / len(opps[t]) for t in teams}
    srs = dict(avg)
    for _ in range(iters):
        nxt = {t: avg[t] + sum(srs[o] for o, _ in opps[t]) / len(opps[t]) for t in teams}
        mean = sum(nxt.values()) / len(nxt)
        srs = {t: nxt[t] - mean for t in teams}
    return {t: round(v, 1) for t, v in srs.items()}


def _odds_line(od, home, fetched):
    def _rng(x, sign):
        if not x:
            return "n/a"
        fmt = "+g" if sign else "g"
        return (f"{format(x['consensus'], fmt)} "
                f"(books {format(x['min'], fmt)}..{format(x['max'], fmt)}, n={x['n']})")
    return (f"**LIVE ODDS @ {fetched}** ({od.get('n_books')} books) — USE THIS FOR THE PICK: "
            f"{home} spread {_rng(od.get('home_spread'), True)} · "
            f"total {_rng(od.get('total'), False)}")


_ESPN_TO_NFLVERSE = {"WSH": "WAS", "LAR": "LA"}  # ESPN abbr -> nflverse abbr


def _espn_team_key(abbr, disp):
    if abbr:
        a = abbr.upper()
        return _ESPN_TO_NFLVERSE.get(a, a)
    if disp:
        return FULLNAME_TO_ABBR.get(disp)
    return None


def fetch_all_injuries():
    """League-wide injuries from ESPN (one request). {abbr: [ "Name (POS) — status" ]}.
    Fail-safe: {'_error':..} / {'_status':'empty'} never raises. Needs
    site.api.espn.com allowlisted; no key."""
    if requests is None:
        return {"_error": "requests missing"}
    try:
        r = requests.get("https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        if r.status_code != 200:
            return {"_error": f"HTTP {r.status_code}"}
        data = r.json()
        actionable = {"out", "doubtful", "questionable"}
        out = {}
        for grp in (data.get("injuries") or []):
            t = grp.get("team") or {}
            key = _espn_team_key(grp.get("abbreviation") or t.get("abbreviation"),
                                 grp.get("displayName") or t.get("displayName") or grp.get("name"))
            lst = []
            for it in (grp.get("injuries") or []):
                st = (it.get("status") or (it.get("type") or {}).get("description") or "").strip()
                if st.lower() not in actionable:
                    continue  # skip IR / day-to-day / season-long; keep game-status only
                ath = it.get("athlete") or {}
                nm = ath.get("displayName") or ath.get("shortName") or "?"
                pos = (ath.get("position") or {}).get("abbreviation") or ""
                lst.append(f"{nm} ({pos}) — {st}".replace(" ()", ""))
            if key:
                out[key] = lst
        return out or {"_status": "empty"}
    except Exception as e:
        return {"_error": type(e).__name__}


def build_dossier(df, game, odds_map=None, inj_map=None, srs_map=None):
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
    # Live odds (fetched once per run, passed in) take priority for the pick.
    od = None
    if odds_map:
        if odds_map.get("_status") == "no_key":
            o.append("**LIVE ODDS:** not fetched (ODDS_API_KEY not set) — pick must use the reference line below and say so.")
        elif odds_map.get("_error"):
            o.append(f"**LIVE ODDS:** unavailable ({odds_map['_error']}) — pick must use the reference line below and say so.")
        else:
            od = odds_map.get((game["home_team"], game["away_team"]))
            if od:
                o.append(_odds_line(od, game["home_team"], odds_map.get("_fetched_at", "run time")))
            else:
                o.append("**LIVE ODDS:** no matching event returned — pick uses the reference line below.")
    o.append(f"**Reference line (nflverse, may be stale):** {ls['spread']} | total {ls['total']} | "
             f"ML {game['away_team']} {ls['away_ml']} ({ls['away_imp']}%) / "
             f"{game['home_team']} {ls['home_ml']} ({ls['home_imp']}%)")
    o.append(f"**Venue:** {ls['stadium']} · {ls['roof']} · {ls['surface']}")
    o.append(f"**Rest:** {rest['away'][0]} {rest['away'][1]} | {rest['home'][0]} {rest['home'][1]} → {rest['note']}")

    # Travel / body clock (road team)
    tv = compute_travel(game)
    if tv and tv["notes"]:
        o.append("**Travel / body clock:** " + " · ".join(tv["notes"]))

    # Power rating (SRS): margin + strength of schedule, vs the number
    if srs_map:
        hs, aw = srs_map.get(game["home_team"]), srs_map.get(game["away_team"])
        if hs is not None and aw is not None:
            implied = round(hs - aw + 2.0, 1)  # +2.0 generic home-field
            o.append(f"**Power rating (SRS, margin+SOS):** {game['home_team']} {hs:+g} vs "
                     f"{game['away_team']} {aw:+g} → SRS sees {game['home_team']} by ~{implied} (incl 2.0 HFA)")

    # Context: division game, spread on a key number
    ctx = []
    if game.get("div_game") in (1, 1.0, True):
        ctx.append("division game")
    spabs = None
    if od and od.get("home_spread"):
        spabs = abs(od["home_spread"]["consensus"])
    elif not pd.isna(game["spread_line"]):
        spabs = abs(float(game["spread_line"]))
    if spabs is not None:
        for kn in (3, 7, 10, 14):
            if abs(spabs - kn) <= 0.5:
                ctx.append(f"spread sits on key number {kn}")
                break
    if ctx:
        o.append("**Context:** " + " · ".join(ctx))

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
    o.append("\n**Injuries (ESPN)**")
    if inj_map is None:
        o.append("- not fetched")
    elif inj_map.get("_error"):
        o.append(f"- unavailable ({inj_map['_error']})")
    elif inj_map.get("_status") == "empty":
        o.append("- none reported leaguewide")
    else:
        for team in (game["away_team"], game["home_team"]):
            lst = inj_map.get(team) or []
            qbs = [x for x in lst if "(QB)" in x]
            if qbs:
                o.append(f"- ⚠ {team} QB: " + "; ".join(qbs))
            if lst:
                o.append(f"- {team}: " + "; ".join(lst[:8]) + (" …" if len(lst) > 8 else ""))
            else:
                o.append(f"- {team}: none reported")

    wx = fetch_weather(game)
    if not wx.get("relevant"):
        o.append("\n**Weather:** indoor — n/a")
    else:
        wline = f"\n**Weather (game-time forecast):** {wx['text']}"
        wmph = wx.get("wind_mph")
        if wmph is not None and wmph >= 15:
            wline += f"  ⚠ WIND {wmph} mph — strong Under signal (degrades passing & kicking)"
        o.append(wline)
    o.append("\n_Data: nflverse + NWS + The Odds API. Any pick is an opinion from historical data, not a placed wager or financial advice._")
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

    odds_map = fetch_all_odds()
    inj_map = fetch_all_injuries()
    srs_map = compute_srs(df, season)
    os.makedirs(a.out, exist_ok=True)
    status = odds_map.get("_status") or odds_map.get("_error") or f"live ({odds_map.get('_remaining','?')} reqs left)"
    injst = inj_map.get("_error") or inj_map.get("_status") or f"{len([k for k in inj_map if not k.startswith('_')])} teams"
    print(f"EdgeBot brief — {len(g)} game(s), {base}..{end}, season {season} · odds: {status} · injuries: {injst}\n")
    for _, r in g.iterrows():
        text = build_dossier(df, r, odds_map, inj_map, srs_map)
        path = os.path.join(a.out, f"{r['game_id']}.md")
        with open(path, "w") as f:
            f.write(text)
        print(f"wrote {path}  ({r['away_team']}@{r['home_team']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
