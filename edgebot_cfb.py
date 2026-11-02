#!/usr/bin/env python3
"""EdgeBot CFB — college football data dossier via CollegeFootballData (CFBD).

Self-contained (same pattern as edgebot_standalone.py) so a scheduled run can
fetch it over raw.githubusercontent.com and run it. Needs CFBD_API_KEY in the
environment and api.collegefootballdata.com allowlisted.

College QB game-by-game splits are not cleanly available, so this leans on
lines, SP+ power ratings, records, recent form, and game context. No wagers
placed or recommended — data points only (any pick is an opinion).

Usage: python3 edgebot_cfb.py --days 1 --out reports/     # today (+N days) CFB
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import statistics
import sys

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

CFBD = "https://api.collegefootballdata.com"
_KEY = os.environ.get("CFBD_API_KEY")
_HDRS = {"Authorization": f"Bearer {_KEY}", "Accept": "application/json"}
try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = None


def g(d, *keys, default=None):
    """First present key (handles CFBD camelCase/snake_case drift)."""
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def _get(path, **params):
    if requests is None:
        raise RuntimeError("requests missing")
    if not _KEY:
        raise RuntimeError("CFBD_API_KEY not set")
    r = requests.get(CFBD + path, headers=_HDRS, params=params, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"{path} HTTP {r.status_code}")
    return r.json()


def _et_date(iso):
    """ISO (UTC) start -> ET date string YYYY-MM-DD."""
    try:
        t = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if _ET is not None:
            t = t.astimezone(_ET)
        return t.date().isoformat()
    except Exception:
        return str(iso)[:10]


# ---------- data pulls (defensive) ---------------------------------------

def load_season(year, season_type="regular"):
    games = _get("/games", year=year, seasonType=season_type)
    lines = _get("/lines", year=year, seasonType=season_type)
    try:
        sp = {g(x, "team"): x for x in _get("/ratings/sp", year=year)}
    except Exception:
        sp = {}
    try:
        rec = {g(x, "team"): x for x in _get("/records", year=year)}
    except Exception:
        rec = {}
    lines_by_id = {g(x, "id"): x for x in lines}
    return {"games": games, "lines_by_id": lines_by_id, "sp": sp, "rec": rec, "year": year}


# ---------- angle helpers -------------------------------------------------

def _consensus_line(line_row):
    if not line_row:
        return None
    provs = line_row.get("lines") or []
    spreads = [p.get("spread") for p in provs if p.get("spread") is not None]
    totals = [p.get("overUnder", p.get("over_under")) for p in provs
              if p.get("overUnder", p.get("over_under")) is not None]
    fav = None
    for p in provs:
        fs = p.get("formattedSpread") or p.get("formatted_spread")
        if fs:
            fav = fs
            break
    return {
        "spread_med": round(statistics.median(spreads), 1) if spreads else None,
        "total_med": round(statistics.median(totals), 1) if totals else None,
        "favored": fav, "n_providers": len(provs),
    }


def _sp_edge(sp, home, away):
    h, a = sp.get(home), sp.get(away)
    if not h or not a:
        return None
    hr, ar = g(h, "rating"), g(a, "rating")
    if hr is None or ar is None:
        return None
    # SP+ margin for home ≈ rating diff + ~2.5 home-field (neutral handled by caller)
    return {"home_rating": round(hr, 1), "away_rating": round(ar, 1),
            "home_rank": g(h, "ranking"), "away_rank": g(a, "ranking"),
            "diff": round(hr - ar, 1)}


def _record(rec, team):
    r = rec.get(team)
    if not r:
        return None
    tot = g(r, "total") or {}
    conf = g(r, "conferenceGames", "conference_games") or {}
    return {"overall": f"{g(tot,'wins',default=0)}-{g(tot,'losses',default=0)}",
            "conf": f"{g(conf,'wins',default=0)}-{g(conf,'losses',default=0)}"}


def _recent_form(games, team, before_date, n=3):
    played = []
    for gm in games:
        hp, ap = g(gm, "homePoints", "home_points"), g(gm, "awayPoints", "away_points")
        if hp is None or ap is None:
            continue
        d = _et_date(g(gm, "startDate", "start_date"))
        if d >= before_date:
            continue
        ht, at = g(gm, "homeTeam", "home_team"), g(gm, "awayTeam", "away_team")
        if team not in (ht, at):
            continue
        if team == ht:
            res = "W" if hp > ap else ("L" if hp < ap else "T")
            played.append((d, f"{res} vs {at} {int(hp)}-{int(ap)}"))
        else:
            res = "W" if ap > hp else ("L" if ap < hp else "T")
            played.append((d, f"{res} @ {ht} {int(ap)}-{int(hp)}"))
    played.sort()
    return [s for _, s in played[-n:]]


# ---------- dossier -------------------------------------------------------

def build_dossier(season, gm):
    games = season["games"]
    home = g(gm, "homeTeam", "home_team")
    away = g(gm, "awayTeam", "away_team")
    gid = g(gm, "id")
    date = _et_date(g(gm, "startDate", "start_date"))
    neutral = g(gm, "neutralSite", "neutral_site", default=False)
    conf_game = g(gm, "conferenceGame", "conference_game", default=False)
    venue = g(gm, "venue", default="?")
    ln = _consensus_line(season["lines_by_id"].get(gid))
    sp = _sp_edge(season["sp"], home, away)
    hrec, arec = _record(season["rec"], home), _record(season["rec"], away)
    hform = _recent_form(games, home, date)
    aform = _recent_form(games, away, date)

    o = []
    vs = "vs" if not neutral else "vs (neutral)"
    o.append(f"# {away} @ {home} — {date} (Wk {g(gm,'week')})"
             + ("  · conf game" if conf_game else "") + ("  · neutral site" if neutral else ""))
    o.append(f"**Venue:** {venue}")
    if ln:
        o.append(f"**Line (CFBD, {ln['n_providers']} books):** {ln['favored'] or 'n/a'} · "
                 f"total {ln['total_med']}")
    else:
        o.append("**Line:** not posted yet")
    if sp:
        hfa = 0 if neutral else 2.5
        implied = round(sp["diff"] + hfa, 1)
        hr = f"#{sp['home_rank']} " if sp.get("home_rank") else ""
        ar = f"#{sp['away_rank']} " if sp.get("away_rank") else ""
        o.append(f"**SP+ power:** {hr}{home} {sp['home_rating']} vs {ar}{away} {sp['away_rating']} "
                 f"→ SP+ sees {home} by ~{implied} (incl {hfa} HFA)")
    else:
        o.append("**SP+ power:** unavailable")
    o.append(f"**Records:** {away} {arec['overall'] if arec else '?'} "
             f"(conf {arec['conf'] if arec else '?'}) · "
             f"{home} {hrec['overall'] if hrec else '?'} (conf {hrec['conf'] if hrec else '?'})")
    o.append(f"**Recent — {away}:** " + ("; ".join(aform) if aform else "n/a"))
    o.append(f"**Recent — {home}:** " + ("; ".join(hform) if hform else "n/a"))
    o.append("\n_Data: CollegeFootballData (SP+, lines, records). QB situational "
             "splits not available for CFB. Any pick is an opinion, not a wager or advice._")
    return "\n".join(o), gid, f"{away}_at_{home}"


# ---------- runner --------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="YYYY-MM-DD (default today ET)")
    p.add_argument("--days", type=int, default=0, help="include this many days after --date")
    p.add_argument("--year", type=int)
    p.add_argument("--season-type", default="regular")
    p.add_argument("--out", default="reports_cfb/")
    a = p.parse_args()

    if not _KEY:
        print("NO_KEY: CFBD_API_KEY not set in environment.")
        return 1
    try:
        base = (dt.date.fromisoformat(a.date) if a.date
                else dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).date())
    except ValueError:
        print(f"bad --date: {a.date}"); return 1
    end = base + dt.timedelta(days=a.days)
    year = a.year or base.year

    try:
        season = load_season(year, a.season_type)
    except Exception as e:
        print(f"FETCH_FAILED: {e}")
        return 1

    sel = []
    for gm in season["games"]:
        d = _et_date(g(gm, "startDate", "start_date"))
        if base.isoformat() <= d <= end.isoformat():
            sel.append((d, gm))
    sel.sort(key=lambda x: x[0])
    if not sel:
        print(f"NO_GAMES: no CFB games {base}..{end} (season {year}).")
        return 0

    os.makedirs(a.out, exist_ok=True)
    print(f"EdgeBot CFB — {len(sel)} game(s), {base}..{end}, season {year}\n")
    for _, gm in sel:
        text, gid, slug = build_dossier(season, gm)
        path = os.path.join(a.out, f"{gid or slug}.md")
        with open(path, "w") as f:
            f.write(text)
        print(f"wrote {path}  ({g(gm,'awayTeam','away_team')} @ {g(gm,'homeTeam','home_team')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
