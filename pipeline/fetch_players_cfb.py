"""Download player data used for the Star Power and Star Performance categories.

Saves, per season, to data/cfb/players/<year>.json:
  - box: every game's notable individual stat lines (from CFBD box scores)
  - totals: each player's season totals, built from those box scores
  - roster: player ids on each team's roster (patchy before ~2009)
and NFL draft picks for every year to data/cfb/draft.json.

Usage:
    python pipeline/fetch_players_cfb.py 2004-2026
    python pipeline/fetch_players_cfb.py current
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_cfb  # noqa: E402  (reuses its API client and helpers)

ROOT = fetch_cfb.ROOT
CACHE = ROOT / "data" / "cfb"
PLAYERS = CACHE / "players"
FIRST_DRAFT = 2005  # first draft that includes players from the 2004 season


# ---------------------------------------------------------------- draft


def fetch_draft(force=False):
    path = CACHE / "draft.json"
    draft = json.loads(path.read_text()) if path.exists() else {"years": [], "picks": {}}
    today = dt.date.today()
    latest = today.year if today.month >= 5 else today.year - 1  # the draft happens in late April
    for year in range(FIRST_DRAFT, latest + 1):
        if year in draft["years"] and not force:
            continue
        print(f"draft {year}")
        for p in fetch_cfb.get("/draft/picks", year=year):
            if p.get("collegeAthleteId") is None:
                continue
            draft["picks"][str(p["collegeAthleteId"])] = [
                p["name"], p.get("collegeTeam"), year, p["round"], p["overall"], p.get("position"),
            ]
        draft["years"] = sorted(set(draft["years"]) | {year})
    path.write_text(json.dumps(draft, separators=(",", ":")))
    return draft


# ---------------------------------------------------------------- box scores


def num(s):
    try:
        return float(str(s).split("/")[0])
    except ValueError:
        return 0.0


STAT_KEYS = ["passCmp", "passAtt", "passYds", "passTD", "passINT", "rushCar", "rushYds", "rushTD",
             "rec", "recYds", "recTD", "sacks", "tfl", "defINT", "defTD", "retYds", "retTD"]


def player_lines(game):
    """{(teamName, athleteId): {"name":..., stats...}} for one game from the box score."""
    out = {}
    for t in game.get("teams", []):
        team = t.get("team")
        for cat in t.get("categories", []):
            cname = cat["name"]
            for typ in cat.get("types", []):
                tname = typ["name"]
                for a in typ.get("athletes", []):
                    aid = str(a.get("id"))
                    if not aid or aid.startswith("-"):
                        continue  # team totals and unnamed players
                    rec = out.setdefault((team, aid), {"name": a.get("name")})
                    v = a.get("stat")
                    if cname == "passing":
                        if tname == "C/ATT" and "/" in str(v):
                            c, att = str(v).split("/")[:2]
                            rec["passCmp"], rec["passAtt"] = num(c), num(att)
                        elif tname in ("YDS", "TD", "INT"):
                            rec[{"YDS": "passYds", "TD": "passTD", "INT": "passINT"}[tname]] = num(v)
                    elif cname == "rushing" and tname in ("CAR", "YDS", "TD"):
                        rec[{"CAR": "rushCar", "YDS": "rushYds", "TD": "rushTD"}[tname]] = num(v)
                    elif cname == "receiving" and tname in ("REC", "YDS", "TD"):
                        rec[{"REC": "rec", "YDS": "recYds", "TD": "recTD"}[tname]] = num(v)
                    elif cname == "defensive" and tname in ("SACKS", "TFL", "TD"):
                        rec[{"SACKS": "sacks", "TFL": "tfl", "TD": "defTD"}[tname]] = num(v)
                    elif cname == "interceptions" and tname in ("INT", "TD"):
                        key = "defINT" if tname == "INT" else "defTD"
                        rec[key] = max(rec.get(key, 0), num(v)) if key == "defTD" else num(v)
                    elif cname in ("kickReturns", "puntReturns") and tname in ("YDS", "TD"):
                        key = "retYds" if tname == "YDS" else "retTD"
                        rec[key] = rec.get(key, 0) + num(v)
    return out


def impact_points(s):
    """A fantasy-style score for one player's game. Vince Young's 2006 Rose Bowl is about 49."""
    g = s.get
    return (
        g("passYds", 0) / 25 + 4 * g("passTD", 0) - 2 * g("passINT", 0)
        + g("rushYds", 0) / 10 + 6 * g("rushTD", 0)
        + g("recYds", 0) / 10 + 6 * g("recTD", 0)
        + 4 * g("sacks", 0) + 1.5 * g("tfl", 0) + 5 * g("defINT", 0) + 6 * g("defTD", 0)
        + g("retYds", 0) / 25 + 6 * g("retTD", 0)
    )


def stat_line(s):
    """Human-readable summary like '30/40, 267 pass yds · 19-200, 3 TD rushing'."""
    g = lambda k: int(s.get(k, 0))  # noqa: E731
    parts = []
    if g("passAtt") or g("passYds"):
        p = f"{g('passCmp')}/{g('passAtt')}, {g('passYds')} pass yds"
        extra = [f"{g('passTD')} TD"] if g("passTD") else []
        if g("passINT"):
            extra.append(f"{g('passINT')} INT")
        parts.append(p + (f", {', '.join(extra)}" if extra else ""))
    if g("rushCar") and (g("rushYds") >= 40 or g("rushTD")):
        parts.append(f"{g('rushCar')}-{g('rushYds')}" + (f", {g('rushTD')} TD" if g("rushTD") else "") + " rushing")
    if g("rec") and (g("recYds") >= 40 or g("recTD")):
        parts.append(f"{g('rec')}-{g('recYds')}" + (f", {g('recTD')} TD" if g("recTD") else "") + " receiving")
    d = []
    if g("sacks"):
        d.append(f"{s['sacks']:g} sacks" if s["sacks"] != 1 else "1 sack")
    if g("defINT"):
        d.append(f"{g('defINT')} INT")
    if g("defTD"):
        d.append(f"{g('defTD')} defensive TD")
    if g("retTD"):
        d.append(f"{g('retTD')} return TD")
    if d:
        parts.append(", ".join(d))
    return " · ".join(parts)


def fetch_season(year, force=False):
    path = PLAYERS / f"{year}.json"
    old = json.loads(path.read_text()) if path.exists() else None
    if old and old.get("complete") and not force:
        print(f"{year}: players cached (complete), skipping")
        return

    season_path = CACHE / f"{year}.json"
    if not season_path.exists():
        print(f"{year}: no games cached yet; run fetch_cfb.py first")
        return
    games = json.loads(season_path.read_text())["games"]
    game_ids = {str(g["id"]) for g in games}

    data = old if old and not force else {"box": {}, "totals": {}, "names": {}, "roster": {}, "tried": []}
    tried = set(data["tried"])
    weeks = sorted({(g["seasonType"], g["week"]) for g in games if str(g["id"]) not in tried})
    for stype, week in weeks:
        print(f"{year}: box scores {stype} week {week}")
        rows = fetch_cfb.get("/games/players", year=year, week=week, seasonType=stype, classification="fbs")
        for game in rows:
            gid = str(game.get("id"))
            if gid not in game_ids or gid in data["box"]:
                continue
            lines = player_lines(game)
            entries = []
            for (team, aid), s in lines.items():
                data["names"][aid] = s["name"]
                tot = data["totals"].setdefault(aid, {"team": team, "g": 0})
                tot["team"] = team  # last team seen this season (handles mid-season oddities)
                tot["g"] += 1
                for k in STAT_KEYS:
                    if s.get(k):
                        tot[k] = tot.get(k, 0) + s[k]
                pts = impact_points(s)
                if pts >= 8:
                    entries.append([aid, team, round(pts, 1), stat_line(s)])
            entries.sort(key=lambda e: -e[2])
            # Keep the stat lines that matter: the top few per game, plus anyone with a big day.
            data["box"][gid] = [e for i, e in enumerate(entries) if i < 4 or e[2] >= 20]
        for g in games:
            if g["seasonType"] == stype and g["week"] == week:
                tried.add(str(g["id"]))

    if not data["roster"] or force:
        print(f"{year}: rosters")
        roster = {}
        for p in fetch_cfb.get("/roster", year=year):
            if p.get("id") and p.get("team"):
                roster.setdefault(p["team"], []).append(str(p["id"]))
        data["roster"] = roster

    data["tried"] = sorted(tried)
    data["complete"] = dt.date.today() >= dt.date(year + 1, 2, 1)
    data["fetchedAt"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    PLAYERS.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":")))
    print(f"{year}: {len(data['box'])} box scores, {len(data['totals'])} players -> {path.relative_to(ROOT)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("years", help="e.g. 2004-2026, 2024, or 'current'")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    fetch_draft(force=args.force)
    for y in fetch_cfb.parse_years(args.years):
        fetch_season(y, force=args.force)
    print(f"done; {fetch_cfb.calls} API calls used")


if __name__ == "__main__":
    main()
