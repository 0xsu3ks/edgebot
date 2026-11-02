# EdgeBot — NFL / CFB betting-data dossier agent

Gathers decision data points for football games — lines, totals, rest/bye spots,
QB situational splits, head-to-head history, referee tendencies, venue/weather —
and writes a per-game **dossier** you read before you bet.

**It does not place wagers and makes no picks.** Data points only.

## Design principle

The LLM is the *analyst*, never the *data source*. Numbers come from
authoritative sports APIs via deterministic scripts; the model only reasons
over a clean, sourced dossier. This is why it avoids the "the API doesn't have
up-to-date info" / hallucinated-stat problem — the facts are fetched, not recalled.

```
authoritative data APIs  ->  src/ (deterministic fetch + compute)  ->  dossier.md  ->  analyst reads
```

## What works today (free, no key)

Powered by the **nflverse** master `games.csv` (reached via `raw.githubusercontent.com`):

- Vegas spread / total / moneylines (home-perspective spread, verified)
- Rest & schedule spot — off-bye (14d) and short-week (<=5d) detection
- QB situational splits computed live: career, home/away, **off a bye**,
  short week, last-10 — straight-up, ATS, and O/U records (no data leakage:
  only games before kickoff are used)
- Head-to-head series (scores, totals vs. line, ATS)
- Referee tendency (over-rate, avg total, home ATS) — once the ref is assigned
- Venue: roof/surface + historical outdoor wind/temp at the stadium

### Run it

```bash
pip install -r requirements.txt
python run.py list  --season 2026 --week 9          # list slate with lines
python run.py game  2026_09_HOU_LAC                 # one dossier to stdout
python run.py slate --season 2026 --week 9 --out reports/   # whole slate to files
```

## Not yet wired — needs YOUR keys + a network-policy change

Two separate blockers, both one-time setup:

1. **Host allowlist.** This cloud environment's network policy is *Limited* — it
   reaches package mirrors and `raw.githubusercontent.com` but denied every live
   API host tested (`api.weather.gov`, `api.the-odds-api.com`, `site.api.espn.com`
   all 403 at the proxy). To add live feeds, the hosts below must be added under
   **Allowed domains** in the environment settings (keep *Allow package managers*
   ticked). See https://code.claude.com/docs/en/cloud-environments#network-access
2. **API keys / logins**, stored as environment secrets.

| Data point | Source | Host to allowlist | Cost |
|---|---|---|---|
| Live line **movement** / price shopping | The Odds API | `api.the-odds-api.com` | ~$10-30/mo |
| **Public betting %** (tickets vs. handle, sharp splits) | Action Network / VSIN | their API hosts | $100+/mo |
| Advanced QB splits, injuries | Stathead / official injury report | their hosts | ~$8/mo + |
| Game-time **weather** forecast | NWS | `api.weather.gov` | free |
| CFB everything | CollegeFootballData | `api.collegefootballdata.com` | free key |

Each is a drop-in module under `src/` returning the same dict shape; `dossier.py`
section 8 lists what's still stubbed.

## Scheduling (the agentic part)

Each slate = one scheduled task firing a fresh session that pulls data and
delivers a dossier via **push + email**. Target cadence:

| Day | Slate |
|---|---|
| Tue AM | Tue/Wed MACtion (CFB) |
| Thu AM | NFL + CFB |
| Fri AM | CFB |
| Sat AM | CFB |
| Sun AM | NFL |
| Mon AM | NFL (MNF) |

Because the container is ephemeral, the **code must live in a git repo** so each
scheduled run clones it fresh. (Set this up before wiring schedules.)

## Layout

```
src/data.py      # fetch + cache nflverse, game lookup, filters
src/angles.py    # line, rest, QB splits, H2H, referee, venue  (pure functions)
src/dossier.py   # assemble markdown dossier
run.py           # CLI: list / game / slate
reports/         # generated dossiers
```
