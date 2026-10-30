"""Download college football data from the CFBD API and cache it per season.

Each season is saved to data/cfb/<year>.json in a compact form (games, betting
lines, polls and a score timeline per game built from play-by-play). Seasons
already cached are skipped, so re-running only costs API calls for new games.

Usage:
    python pipeline/fetch_cfb.py 2004-2025        # backfill a range
    python pipeline/fetch_cfb.py current          # refresh the current season
    python pipeline/fetch_cfb.py 2024 --force     # re-download one season

Needs CFBD_API_KEY in the environment.
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import requests

API = "https://api.collegefootballdata.com"
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cfb"

session = requests.Session()
calls = 0


def get(path, **params):
    global calls
    key = os.environ.get("CFBD_API_KEY")
    if not key:
        sys.exit("CFBD_API_KEY is not set")
    for attempt in range(5):
        try:
            r = session.get(
                API + path,
                params=params,
                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                timeout=120,
            )
            calls += 1
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"{r.status_code} {r.text[:200]}")
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            wait = 2 ** (attempt + 1)
            print(f"  ! {path} {params} failed ({e}); retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"giving up on {path} {params}")


# ---------------------------------------------------------------- games

GAME_KEYS = {
    "id": "id", "season": "season", "week": "week", "seasonType": "seasonType",
    "startDate": "date", "neutralSite": "neutral", "conferenceGame": "confGame",
    "venue": "venue", "attendance": "attendance", "notes": "notes",
    "homeId": "hId", "homeTeam": "home", "homeConference": "hConf",
    "homeClassification": "hClass", "homePoints": "hPts", "homeLineScores": "hLine",
    "homePregameElo": "hElo",
    "awayId": "aId", "awayTeam": "away", "awayConference": "aConf",
    "awayClassification": "aClass", "awayPoints": "aPts", "awayLineScores": "aLine",
    "awayPregameElo": "aElo",
    "excitementIndex": "excitement",
}


def compact_game(g):
    out = {short: g.get(long) for long, short in GAME_KEYS.items()}
    p = g.get("playoff")
    if p:
        out["playoff"] = {k: p.get(k) for k in ("competition", "round", "roundName", "homeSeed", "awaySeed", "bowlName")}
    return out


def compact_lines(rows):
    """Map gameId -> {spread, overUnder} (home perspective; negative = home favored)."""
    out = {}
    for row in rows:
        lines = row.get("lines") or []
        spreads = [l["spread"] for l in lines if l.get("spread") is not None]
        totals = [l["overUnder"] for l in lines if l.get("overUnder") is not None]
        consensus = next((l for l in lines if l.get("provider") == "consensus"), None)
        if consensus and consensus.get("spread") is not None:
            spread = consensus["spread"]
        elif spreads:
            spread = sum(spreads) / len(spreads)
        else:
            spread = None
        if spread is None and not totals:
            continue
        out[str(row["id"])] = {
            "spread": round(spread, 1) if spread is not None else None,
            "overUnder": round(sum(totals) / len(totals), 1) if totals else None,
        }
    return out


def compact_rankings(rows):
    """Map "<seasonType>-<week>" -> {teamId: rank}, preferring the AP poll."""
    out = {}
    for wk in rows:
        polls = {p["poll"]: p for p in wk.get("polls", [])}
        poll = polls.get("AP Top 25") or polls.get("Coaches Poll")
        if not poll:
            continue
        out[f"{wk['seasonType']}-{wk['week']}"] = {
            str(r["teamId"]): r["rank"] for r in poll["ranks"] if r.get("teamId") is not None
        }
    return out


# ---------------------------------------------------------------- timelines

REG_SECONDS = 3600
OT_SECONDS = 300  # each overtime period is drawn as 5 "virtual" minutes


def build_timelines(plays, games_by_id):
    """Turn raw plays into a score timeline per game: [[elapsedSeconds, home, away], ...]."""
    by_game = {}
    for p in plays:
        gid = p.get("gameId")
        if gid in games_by_id:
            by_game.setdefault(gid, []).append(p)

    out = {}
    for gid, ps in by_game.items():
        g = games_by_id[gid]

        def clock_secs(p):
            c = p.get("clock") or {}
            return (c.get("minutes") or 0) * 60 + (c.get("seconds") or 0)

        ps.sort(key=lambda p: (p.get("period") or 0, -clock_secs(p), int(p.get("id") or 0)))
        ot_counts = {}
        for p in ps:
            per = p.get("period") or 0
            if per > 4:
                ot_counts[per] = ot_counts.get(per, 0) + 1

        final = (g.get("homePoints"), g.get("awayPoints"))
        states = [[0, 0, 0]]
        ot_seen = {}
        for p in ps:
            per = p.get("period") or 0
            if per < 1:
                continue
            if p.get("offense") == p.get("home"):
                h, a = p.get("offenseScore"), p.get("defenseScore")
            else:
                h, a = p.get("defenseScore"), p.get("offenseScore")
            if h is None or a is None:
                continue
            if per <= 4:
                t = (per - 1) * 900 + (900 - min(clock_secs(p), 900))
            else:
                k = ot_seen[per] = ot_seen.get(per, 0) + 1
                t = REG_SECONDS + (per - 5) * OT_SECONDS + round(OT_SECONDS * k / (ot_counts[per] + 1))
            last = states[-1]
            if (h, a) == (last[1], last[2]):
                continue
            # Ignore glitches: scores that go backwards or exceed the final.
            if h < last[1] or a < last[2] or h > final[0] or a > final[1]:
                continue
            states.append([t, h, a])

        last = states[-1]
        max_period = max((p.get("period") or 0) for p in ps)
        end_t = REG_SECONDS + OT_SECONDS * max(0, max_period - 4)
        if (last[1], last[2]) != final:
            states.append([end_t, final[0], final[1]])
        out[str(gid)] = {"s": states, "end": end_t}
    return out


# ---------------------------------------------------------------- seasons


def season_path(year):
    return CACHE / f"{year}.json"


def fetch_season(year, force=False):
    path = season_path(year)
    old = json.loads(path.read_text()) if path.exists() else None
    if old and old.get("complete") and not force:
        print(f"{year}: cached (complete), skipping")
        return

    print(f"{year}: fetching games, lines, polls")
    raw_games = get("/games", year=year, seasonType="both", classification="fbs")
    done = [g for g in raw_games if g.get("completed") and g.get("homePoints") is not None and g.get("awayPoints") is not None]
    games_by_id = {g["id"]: g for g in done}

    lines = compact_lines(get("/lines", year=year))
    rankings = compact_rankings(get("/rankings", year=year))

    timelines = {} if force or not old else dict(old.get("timelines", {}))
    tried = set() if force or not old else set(old.get("timelinesTried", []))
    weeks = sorted({(g["seasonType"], g["week"]) for g in done if str(g["id"]) not in timelines and str(g["id"]) not in tried})
    for stype, week in weeks:
        print(f"{year}: plays {stype} week {week}")
        plays = get("/plays", year=year, week=week, seasonType=stype, classification="fbs")
        timelines.update(build_timelines(plays, games_by_id))
        for g in done:
            if g["seasonType"] == stype and g["week"] == week:
                tried.add(str(g["id"]))

    # A season is "complete" once its postseason has been played; it won't be re-fetched.
    complete = dt.date.today() >= dt.date(year + 1, 2, 1)

    data = {
        "season": year,
        "fetchedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "complete": complete,
        "games": [compact_game(g) for g in done],
        "lines": lines,
        "rankings": rankings,
        "timelines": timelines,
        "timelinesTried": sorted(tried),
    }
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":")))
    print(f"{year}: {len(done)} games, {len(timelines)} timelines, {len(lines)} lines -> {path.relative_to(ROOT)}")


def fetch_teams():
    teams = get("/teams")
    out = {}
    for t in teams:
        logos = t.get("logos") or []
        out[str(t["id"])] = {
            "school": t.get("school"),
            "mascot": t.get("mascot"),
            "abbr": t.get("abbreviation"),
            "color": t.get("color"),
            "alt": t.get("alternateColor"),
            "logo": logos[0] if logos else None,
            "logoDark": logos[1] if len(logos) > 1 else None,
        }
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "teams.json").write_text(json.dumps(out, separators=(",", ":")))
    print(f"teams: {len(out)}")


def current_season():
    today = dt.date.today()
    return today.year if today.month >= 7 else today.year - 1


def parse_years(spec):
    if spec == "current":
        return [current_season()]
    if "-" in spec:
        a, b = spec.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(spec)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("years", help="e.g. 2004-2025, 2024, or 'current'")
    ap.add_argument("--force", action="store_true", help="re-download even if cached")
    ap.add_argument("--teams", action="store_true", help="also refresh team logos/colors")
    args = ap.parse_args()

    if args.teams or not (CACHE / "teams.json").exists():
        fetch_teams()
    for y in parse_years(args.years):
        fetch_season(y, force=args.force)
    print(f"done; {calls} API calls used")


if __name__ == "__main__":
    main()
