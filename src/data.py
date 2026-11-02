"""Data layer: load nflverse game data (schedules + Vegas lines + context).

The nflverse master games.csv carries, for every NFL game back to 1999 and
forward to the currently-scheduled slate:
  - matchup, kickoff, week, season, roof/surface/stadium
  - Vegas lines: spread_line (home perspective), total_line, moneylines, prices
  - away_rest / home_rest (days since last game -> bye & short-week detection)
  - away_qb_name / home_qb_name, coaches, referee
  - historical temp / wind (actuals, not forecast)

This module is pure, deterministic data access -- NO analysis, NO LLM.
"""
from __future__ import annotations

import os
import time
import pandas as pd

RAW_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "games.csv")
CACHE_MAX_AGE_H = 6  # refetch if cache older than this


def _cache_fresh(path: str) -> bool:
    if not os.path.exists(path):
        return False
    age_h = (time.time() - os.path.getmtime(path)) / 3600
    return age_h < CACHE_MAX_AGE_H


def load_games(force_refresh: bool = False) -> pd.DataFrame:
    """Return the full games DataFrame, fetching/caching from nflverse."""
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    if force_refresh or not _cache_fresh(CACHE):
        try:
            df = pd.read_csv(RAW_URL)
            df.to_csv(CACHE, index=False)
        except Exception:
            if os.path.exists(CACHE):
                df = pd.read_csv(CACHE)  # fall back to stale cache
            else:
                raise
    else:
        df = pd.read_csv(CACHE)
    # tidy string columns that nflverse ships with stray whitespace
    for col in ("referee", "away_qb_name", "home_qb_name", "away_coach", "home_coach"):
        if col in df.columns:
            df[col] = df[col].astype("string").str.strip()
    return df


def upcoming_games(df: pd.DataFrame, season: int | None = None,
                   week: int | None = None, with_lines: bool = True) -> pd.DataFrame:
    """Unplayed games (no score yet), optionally filtered to season/week and
    to games that already have a posted spread."""
    g = df[df["away_score"].isna()].copy()
    if season is not None:
        g = g[g["season"] == season]
    if week is not None:
        g = g[g["week"] == week]
    if with_lines:
        g = g[g["spread_line"].notna()]
    return g.sort_values("gameday")


def get_game(df: pd.DataFrame, game_id: str) -> pd.Series:
    rows = df[df["game_id"] == game_id]
    if rows.empty:
        raise KeyError(f"game_id not found: {game_id}")
    return rows.iloc[0]


def played(df: pd.DataFrame) -> pd.DataFrame:
    """Only completed games (have a result)."""
    return df[df["result"].notna()].copy()
