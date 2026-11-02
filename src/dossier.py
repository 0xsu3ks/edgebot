"""Assemble a per-game markdown dossier from the computed angles.

This is the hand-off the LLM analyst (or you) reads. It is ONLY facts and
computed splits -- no picks, no wagers. Sections clearly mark any data point
that could not be sourced in the current environment.
"""
from __future__ import annotations

import pandas as pd
from . import data as D
from . import angles as A


def _rec_line(label: str, rec: dict) -> str:
    if not rec or rec.get("games", 0) == 0:
        return f"- **{label}:** no games"
    bits = [f"{rec['games']} g"]
    if rec.get("su"):
        bits.append(f"SU {rec['su']} ({rec['su_pct']}%)")
    if rec.get("ats"):
        atsp = f" ({rec['ats_pct']}%)" if rec.get("ats_pct") is not None else ""
        bits.append(f"ATS {rec['ats']}{atsp}")
    if rec.get("over_pct") is not None:
        bits.append(f"O/U {rec['over']} (O {rec['over_pct']}%)")
    return f"- **{label}:** " + " · ".join(bits)


def build_dossier(df: pd.DataFrame, game_id: str) -> str:
    game = D.get_game(df, game_id)
    pl = D.played(df)
    # only use games that happened BEFORE this one for splits (no leakage)
    pl = pl[pl["gameday"] < game["gameday"]]

    ls = A.line_summary(game)
    rest = A.rest_edge(game)
    h2h = A.head_to_head(pl, game["home_team"], game["away_team"])
    ref = A.referee_tendency(pl, game.get("referee"))
    vw = A.venue_weather(game, pl)
    hqb = A.qb_splits(pl, game.get("home_qb_name"), "home")
    aqb = A.qb_splits(pl, game.get("away_qb_name"), "away")

    out = []
    w = out.append

    w(f"# Edge Dossier — {ls['matchup']}")
    w(f"*{game['weekday']} {game['gameday']} {game['gametime']} ET · "
      f"{game['season']} Week {int(game['week'])} · game_id `{game_id}`*\n")

    w("## 1. The number")
    w(f"- **Line:** {ls['spread']}  |  **Total:** {ls['total']}")
    w(f"- **Moneyline:** {ls['matchup'].split(' @ ')[0]} {ls['away_ml']} "
      f"(implied {ls['away_implied_win_pct']}%) · "
      f"{game['home_team']} {ls['home_ml']} (implied {ls['home_implied_win_pct']}%)")
    w(f"- **Venue:** {ls['stadium']} · roof: {ls['roof']} · surface: {ls['surface']}\n")

    w("## 2. Rest & schedule spot")
    w(f"- {rest['away']['team']} (away): rest {rest['away']['rest']}")
    w(f"- {rest['home']['team']} (home): rest {rest['home']['rest']}")
    w(f"- **Edge:** {rest['note']}\n")

    w(f"## 3. QB situational splits — {game['away_team']} ({aqb.get('qb')})")
    if aqb.get("note"):
        w(f"- _{aqb['note']}_\n")
    else:
        w(_rec_line("Career (in data)", aqb["career_in_data"]))
        w(_rec_line("As road QB", aqb["as_away"]))
        w(_rec_line("Off a bye", aqb["off_bye"]))
        w(_rec_line("Short week", aqb["short_week"]))
        w(_rec_line("Last 10 starts", aqb["last_10"]))
        w("")

    w(f"## 4. QB situational splits — {game['home_team']} ({hqb.get('qb')})")
    if hqb.get("note"):
        w(f"- _{hqb['note']}_\n")
    else:
        w(_rec_line("Career (in data)", hqb["career_in_data"]))
        w(_rec_line("As home QB", hqb["as_home"]))
        w(_rec_line("Off a bye", hqb["off_bye"]))
        w(_rec_line("Short week", hqb["short_week"]))
        w(_rec_line("Last 10 starts", hqb["last_10"]))
        w("")

    w("## 5. Head-to-head series")
    if h2h["n"] == 0:
        w("- No recent meetings in dataset.\n")
    else:
        home_ats_key = f"{game['home_team']}_ats_in_series"
        w(f"- Last {h2h['n']} meetings · series O/U over-rate: {h2h['over_rate_pct']}% · "
          f"{game['home_team']} ATS in series: {h2h[home_ats_key]}")
        for g in h2h["meetings"]:
            tl = f" (total line {g['total_line']})" if g["total_line"] is not None else ""
            w(f"  - {g['date']}: {g['score']} — {g['total_pts']} pts{tl}")
        w("")

    w("## 6. Referee tendency")
    if ref.get("games", 0) == 0:
        w(f"- {ref.get('referee')}: {ref.get('note')}\n")
    else:
        w(f"- **{ref['referee']}** — {ref['games']} g (last 6 seasons): "
          f"over-rate {ref['over_rate_pct']}% · avg total {ref['avg_total_pts']} pts · "
          f"home ATS {ref['home_ats_pct']}%")
        w(f"- _{ref['avg_penalty_note']}_\n")

    w("## 7. Weather / conditions")
    w(f"- Roof: {vw['roof']} · surface: {vw['surface']} · weather relevant: {vw['weather_relevant']}")
    if vw["weather_relevant"]:
        w(f"- Historical outdoor averages here: wind {vw['historical_outdoor_avg_wind_mph']} mph, "
          f"temp {vw['historical_outdoor_avg_temp_f']}°F")
    w(f"- **Live game-time forecast:** {vw['LIVE_FORECAST']}\n")

    w("## 8. Not yet wired (needs your keys / host allowlist)")
    w("- Live multi-book line **movement** & price shopping — The Odds API")
    w("- **Public betting %** (tickets vs. handle / sharp splits) — Action Network, VSIN")
    w("- Prebuilt advanced QB splits & injuries — Stathead, official injury report")
    w("")
    w("_Data: nflverse (schedules, Vegas lines, rest, QB, referee, venue). "
      "No wager is placed or recommended — data points only._")
    return "\n".join(out)
