"""Grade every cached college football game and write the site's data files.

Reads data/cfb/<year>.json (from fetch_cfb.py) and writes:
    site/data/cfb/games.json            one compact record per game
    site/data/cfb/timelines/<year>.json score timeline per game (for charts)
    site/data/cfb/meta.json             rubric, teams, seasons, conferences

Each game gets six category scores between 0 and 1. The site combines them with
the weights in config/rubric.json (and lets visitors adjust them), so this
script only measures; it doesn't decide how much each category counts.

Usage:
    python pipeline/grade_cfb.py
    python pipeline/grade_cfb.py --top 25    # also print the top games
"""

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stars_cfb import StarIndex, star_power_raw  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cfb"
OUT = ROOT / "site" / "data" / "cfb"
RUBRIC = json.loads((ROOT / "config" / "rubric.json").read_text())

REG = 3600

RIVALRIES = {
    frozenset(p)
    for p in [
        ("Alabama", "Auburn"), ("Ohio State", "Michigan"), ("Oklahoma", "Texas"),
        ("USC", "Notre Dame"), ("USC", "UCLA"), ("Florida", "Georgia"),
        ("Florida", "Florida State"), ("Florida State", "Miami"), ("Clemson", "South Carolina"),
        ("Georgia", "Georgia Tech"), ("Army", "Navy"), ("Stanford", "California"),
        ("Washington", "Washington State"), ("Oregon", "Oregon State"), ("Ole Miss", "Mississippi State"),
        ("Alabama", "LSU"), ("Texas", "Texas A&M"), ("Oklahoma", "Oklahoma State"),
        ("Kansas", "Kansas State"), ("Iowa", "Iowa State"), ("Minnesota", "Wisconsin"),
        ("Michigan", "Michigan State"), ("Michigan", "Notre Dame"), ("Penn State", "Ohio State"),
        ("Alabama", "Tennessee"), ("Florida", "Tennessee"), ("Virginia", "Virginia Tech"),
        ("North Carolina", "NC State"), ("North Carolina", "Duke"), ("Pittsburgh", "West Virginia"),
        ("BYU", "Utah"), ("Arizona", "Arizona State"), ("Colorado", "Colorado State"),
        ("Louisville", "Kentucky"), ("Indiana", "Purdue"), ("Auburn", "Georgia"),
        ("Nebraska", "Oklahoma"), ("Baylor", "TCU"), ("Miami", "Florida"), ("Arkansas", "LSU"),
        ("Missouri", "Kansas"), ("Air Force", "Navy"), ("Air Force", "Army"),
        ("Illinois", "Northwestern"), ("Iowa", "Wisconsin"), ("Oregon", "Washington"),
        ("Iowa", "Nebraska"), ("Iowa", "Minnesota"), ("Texas", "Texas Tech"), ("Cal", "Stanford"),
        ("Tennessee", "Vanderbilt"), ("Kentucky", "Tennessee"), ("Boston College", "Notre Dame"),
        ("Utah", "Utah State"), ("Cincinnati", "Miami (OH)"), ("Toledo", "Bowling Green"),
    ]
}

STAGE_VALUE = {
    "title": 1.0, "cfp_sf": 0.85, "cfp_qf": 0.75, "cfp_r1": 0.6,
    "major_bowl": 0.55, "conf_champ": 0.5, "bowl": 0.25, "regular": 0.0,
}
STAGE_LABEL = {
    "title": "National Championship", "cfp_sf": "CFP Semifinal", "cfp_qf": "CFP Quarterfinal",
    "cfp_r1": "CFP First Round", "major_bowl": "Major Bowl", "conf_champ": "Conference Championship",
    "bowl": "Bowl", "regular": None,
}
MAJOR_BOWLS = ("ROSE BOWL", "SUGAR BOWL", "ORANGE BOWL", "FIESTA BOWL", "COTTON BOWL", "PEACH BOWL")


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# ---------------------------------------------------------------- context


def stage_of(g, home_rank, away_rank):
    p = g.get("playoff") or {}
    notes = (g.get("notes") or "").upper()
    if p.get("competition") == "cfp":
        return {"championship": "title", "semifinal": "cfp_sf", "quarterfinal": "cfp_qf"}.get(p.get("round"), "cfp_r1")
    if g["seasonType"] == "postseason":
        if "NATIONAL CHAMPIONSHIP" in notes:
            return "title"
        if "PLAYOFF SEMIFINAL" in notes:
            return "cfp_sf"
        if "PLAYOFF QUARTERFINAL" in notes:
            return "cfp_qf"
        if "PLAYOFF FIRST ROUND" in notes:
            return "cfp_r1"
        if g["season"] < 2014 and {home_rank, away_rank} == {1, 2}:
            return "title"  # BCS title games are often labeled only with the host bowl's name
        if any(b in notes for b in MAJOR_BOWLS):
            return "major_bowl"
        return "bowl"
    if "CHAMPIONSHIP" in notes and "KICKOFF" not in notes and "CLASSIC" not in notes:
        return "conf_champ"
    return "regular"


def poll_for(rankings, g):
    """AP ranks in effect for this game (latest regular-season poll for bowls)."""
    if g["seasonType"] == "postseason":
        weeks = [int(k.split("-")[1]) for k in rankings if k.startswith("regular-")]
        return rankings.get(f"regular-{max(weeks)}", {}) if weeks else {}
    week = g["week"]
    for w in range(week, 0, -1):
        if f"regular-{w}" in rankings:
            return rankings[f"regular-{w}"]
    return {}


def local_date(iso):
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (t - dt.timedelta(hours=5)).date().isoformat()  # US Eastern-ish, so night games keep their date


# ---------------------------------------------------------------- timeline metrics


def timeline_from_quarters(g):
    """Coarse timeline from quarter line scores, for games without play-by-play."""
    hl, al = g.get("hLine") or [], g.get("aLine") or []
    if not hl or len(hl) != len(al):
        return None
    states, h, a = [[0, 0, 0]], 0, 0
    for i, (hq, aq) in enumerate(zip(hl, al)):
        h += hq or 0
        a += aq or 0
        t = (i + 1) * 900 if i < 4 else REG + (i - 3) * 300
        if (h, a) != (states[-1][1], states[-1][2]):
            states.append([t, h, a])
    if (h, a) != (g["hPts"], g["aPts"]):
        return None
    return {"s": states, "end": max(REG, states[-1][0])}


def timeline_metrics(tl, home_won):
    states, end = tl["s"], tl["end"]
    sign = 1 if home_won else -1  # positive margin = winner ahead
    lead_changes = ties = 0
    last_leader = 0
    winner_max_deficit = 0
    within8 = 0.0
    decided_t = 0
    win_type = None  # how the winner took the lead for good: "TD", "FG" or "Safety"
    late_swings = 0  # lead changes or ties in the last 5 minutes of regulation or in OT
    trailed_in_4th_by = 0

    for i, (t, h, a) in enumerate(states):
        m = (h - a) * sign
        nxt = states[i + 1][0] if i + 1 < len(states) else end
        if abs(m) <= 8:
            within8 += max(0, nxt - t)
        winner_max_deficit = max(winner_max_deficit, -m)
        if t >= 2700 or nxt > 2700:
            if m < 0:
                trailed_in_4th_by = max(trailed_in_4th_by, -m)
        if i == 0:
            continue
        leader = (m > 0) - (m < 0)
        if leader == 0:
            ties += 1
            if t >= REG - 300:
                late_swings += 1
        elif last_leader != 0 and leader != last_leader:
            lead_changes += 1
            if t >= REG - 300:
                late_swings += 1
        if leader != 0:
            last_leader = leader
        prev_m = (states[i - 1][1] - states[i - 1][2]) * sign
        if m > 0 and prev_m <= 0:
            decided_t = t  # winner took the lead for good (latest such moment)
            win_type = score_type(states, i, sign)

    return {
        "leadChanges": lead_changes,
        "ties": ties,
        "comeback": winner_max_deficit,
        "trailed4th": trailed_in_4th_by,
        "within8": within8 / end if end else 0,
        "decidedT": decided_t,
        "winType": win_type,
        "lateSwings": late_swings,
    }


def winner_points(states, i, sign):
    """Points the winner scored going into state i."""
    (_, h0, a0), (_, h1, a1) = states[i - 1], states[i]
    return (h1 - h0) if sign > 0 else (a1 - a0)


def score_type(states, i, sign):
    """Classify the go-ahead score at state i as a touchdown, field goal or safety."""
    pts = winner_points(states, i, sign)
    if pts >= 6:
        return "TD"
    if pts == 3:
        return "FG"
    # An extra point or two-point try that breaks a tie belongs to the touchdown just before it.
    if pts in (1, 2) and i >= 2 and winner_points(states, i - 1, sign) == 6 and states[i][0] - states[i - 1][0] <= 60:
        return "TD"
    if pts == 2:
        return "Safety"
    return None


# ---------------------------------------------------------------- categories


def competitiveness(margin, tm):
    margin_s = clamp(1 - (margin - 1) / 27) ** 1.5
    return 0.55 * margin_s + 0.45 * tm["within8"]


def drama(tm, excitement):
    lc = clamp(tm["leadChanges"] / 6)
    ti = clamp(tm["ties"] / 4)
    cb = clamp(tm["comeback"] / 21) ** 0.8
    if tm["trailed4th"] >= 8:
        cb = clamp(cb + 0.15)
    late = clamp(tm["lateSwings"] / 3)  # lead changes/ties in the last 5 minutes or OT
    parts = 0.25 * lc + 0.08 * ti + 0.30 * cb + 0.17 * late
    if excitement is None:
        return parts / 0.80
    return parts + 0.20 * clamp(excitement / 9)


def finish(margin, ot, tm, coarse):
    td = tm["winType"] == "TD"  # a game-winning touchdown beats a game-winning field goal
    if ot:
        s = 0.75 + 0.08 * (ot - 1) + (0.08 if td else 0)
    else:
        left = REG - tm["decidedT"]
        if coarse:  # only know the quarter: give partial credit for a 4th-quarter go-ahead
            s = 0.45 if tm["decidedT"] >= REG else 0.0
        elif left <= 10:
            s = 1.0 if td else 0.85
        elif left <= 60:
            s = 0.92 if td else 0.82
        elif left <= 120:
            s = 0.82 if td else 0.75
        elif left <= 300:
            s = 0.6
        elif left <= 600:
            s = 0.4
        elif left <= 900:
            s = 0.25
        else:
            s = 0.0
        s = max(s, 0.35 if margin <= 3 else 0.15 if margin <= 8 else 0.0)
    return clamp(s + 0.05 * max(0, tm["lateSwings"] - 1))


def rank_value(r):
    return (26 - r) / 25 if r else 0.0


def stakes(stage, rival, hr, ar):
    rs = (rank_value(hr) + rank_value(ar)) / 2
    if hr and ar:
        rs = clamp(rs + 0.2)
    return 1 - (1 - STAGE_VALUE[stage]) * (1 - (0.3 if rival else 0)) * (1 - 0.7 * rs)


def winner_pregame_prob(g, home_won, line):
    """Pregame win probability of the eventual winner, plus where it came from."""
    if line and line.get("spread") is not None:
        p_home = norm_cdf(-line["spread"] / 15)
        src = "spread"
    elif g.get("hElo") and g.get("aElo"):
        hfa = 0 if g.get("neutral") else 55
        p_home = 1 / (1 + 10 ** ((g["aElo"] - g["hElo"] - hfa) / 400))
        src = "elo"
    else:
        return None, None
    return (p_home if home_won else 1 - p_home), src


def upset(win_prob, winner_rank, loser_rank):
    s = clamp((0.5 - win_prob) / 0.4) if win_prob is not None else 0.0
    if loser_rank and not winner_rank:
        s = max(s, 0.4 * rank_value(loser_rank))
    return s


def fireworks(hp, ap):
    s = clamp((hp + ap - 40) / 45)
    if hp >= 35 and ap >= 35:
        s += 0.2
    return clamp(s)


# ---------------------------------------------------------------- main


def grade_season(season, stars):
    games_out, timelines_out = [], {}
    rankings, lines, tls = season["rankings"], season["lines"], season["timelines"]

    for g in season["games"]:
        gid = str(g["id"])
        hp, ap = g["hPts"], g["aPts"]
        if hp == ap:
            continue  # pre-OT ties don't occur in this data range, skip anything odd
        home_won = hp > ap
        margin = abs(hp - ap)
        ot = max(0, len(g.get("hLine") or []) - 4)

        tl = tls.get(gid)
        coarse = False
        if not tl:
            tl = timeline_from_quarters(g)
            coarse = True
        if not tl:
            continue  # no way to judge the flow of the game
        if ot == 0 and tl["end"] > REG:
            ot = math.ceil((tl["end"] - REG) / 300)
        if ot:
            tl = {**tl, "end": REG + 300 * ot}  # run the chart to the end of the last OT period
        tm = timeline_metrics(tl, home_won)

        poll = poll_for(rankings, g)
        hr, ar = poll.get(str(g["hId"])), poll.get(str(g["aId"]))
        stage = stage_of(g, hr, ar)
        rival = frozenset((g["home"], g["away"])) in RIVALRIES or "RIVALRY" in (g.get("notes") or "").upper()
        line = lines.get(gid)
        wp, wp_src = winner_pregame_prob(g, home_won, line)
        winner_rank, loser_rank = (hr, ar) if home_won else (ar, hr)

        # Star Power and Star Performance (raw here; scaled across all games in main()).
        h_total, h_stars, h_est = stars.team_stars(g["home"], g["season"])
        a_total, a_stars, a_est = stars.team_stars(g["away"], g["season"])
        perfs = []
        for name, team, pts, line_txt in stars.performances(g["season"], g["id"]):
            is_home = team == g["home"]
            opp_rank = ar if is_home else hr
            won = is_home == home_won
            ctx = 0.7 + 0.3 * max(STAGE_VALUE[stage], rank_value(opp_rank)) + (0.1 if won else 0)
            perfs.append((pts * ctx, name, "home" if is_home else "away", line_txt, pts))
        perfs.sort(key=lambda x: -x[0])
        perf_raw = (perfs[0][0] + 0.25 * perfs[1][0]) if len(perfs) > 1 else (perfs[0][0] if perfs else 0)
        r1 = sum(1 for lst in (h_stars, a_stars) for v, _, lab in lst if lab.startswith("1st-round"))

        cats = [
            competitiveness(margin, tm),
            drama(tm, g.get("excitement")),
            finish(margin, ot, tm, coarse),
            stakes(stage, rival, hr, ar),
            upset(wp, winner_rank, loser_rank),
            fireworks(hp, ap),
        ]

        tags = []
        if ot:
            tags.append("OT" if ot == 1 else f"{ot}OT")
        if not ot and not coarse and REG - tm["decidedT"] <= 10:
            tags.append(f"Walk-off {tm['winType']}" if tm["winType"] in ("TD", "FG") else "Walk-off")
        elif not ot and not coarse and REG - tm["decidedT"] <= 120:
            tags.append("Late winner")
        if tm["comeback"] >= 14:
            tags.append(f"Comeback ({tm['comeback']})")
        if wp is not None and wp < 0.25:
            tags.append("Upset")
        if hr and ar and hr <= 10 and ar <= 10:
            tags.append("Top-10 matchup")
        if rival:
            tags.append("Rivalry")
        if hp >= 35 and ap >= 35:
            tags.append("Shootout")

        p = g.get("playoff") or {}
        games_out.append(drop_empty({
            "id": g["id"],
            "season": g["season"],
            "week": g["week"],
            "type": "post" if g["seasonType"] == "postseason" else "reg",
            "date": local_date(g["date"]),
            "neutral": bool(g.get("neutral")),
            "conf": bool(g.get("confGame")),
            "venue": g.get("venue"),
            "notes": (p.get("bowlName") or g.get("notes") or None),
            "stage": stage,
            "stageLabel": STAGE_LABEL[stage],
            "home": {"id": g["hId"], "name": g["home"], "conf": g.get("hConf"), "fbs": g.get("hClass") == "fbs",
                      "pts": hp, "rank": hr, "seed": p.get("homeSeed"), "line": g.get("hLine")},
            "away": {"id": g["aId"], "name": g["away"], "conf": g.get("aConf"), "fbs": g.get("aClass") == "fbs",
                      "pts": ap, "rank": ar, "seed": p.get("awaySeed"), "line": g.get("aLine")},
            "cats": [round(c, 3) for c in cats],
            "m": {
                "margin": margin, "total": hp + ap, "ot": ot,
                "leadChanges": tm["leadChanges"], "ties": tm["ties"], "comeback": tm["comeback"],
                "decidedLeft": None if coarse or ot else max(0, REG - tm["decidedT"]),
                "winType": None if coarse else tm["winType"],
                "excitement": round(g["excitement"], 2) if g.get("excitement") is not None else None,
                "spread": line.get("spread") if line else None,
                "winProb": round(wp, 3) if wp is not None else None, "winProbSrc": wp_src,
                "detail": "quarters" if coarse else "plays",
            },
            "tags": tags,
            "stars": {
                "home": [[n, lab] for v, n, lab in h_stars[:4] if v >= 0.5],
                "away": [[n, lab] for v, n, lab in a_stars[:4] if v >= 0.5],
            },
            "perf": [[n, side, txt, round(pts, 1)] for _, n, side, txt, pts in perfs[:2]],
            "_star": star_power_raw(h_total, a_total),
            "_perf": perf_raw,
            "_r1": r1,
            "_starEst": h_est or a_est,
        }))
        timelines_out[gid] = tl
    return games_out, timelines_out


def drop_empty(obj):
    """Remove None values (and False flags) to keep the site's data file small."""
    if isinstance(obj, dict):
        return {k: drop_empty(v) for k, v in obj.items() if v is not None and v is not False}
    return obj


def percentile(values, q):
    vals = sorted(values)
    return vals[min(len(vals) - 1, int(q * len(vals)))] if vals else 1.0


def scale_star_categories(games):
    """Turn raw Star Power / Star Performance into 0-1 scores against the whole database."""
    star_top = percentile([g["_star"] for g in games], 0.98) or 1
    perf_top = percentile([g["_perf"] for g in games if g["_perf"] > 0], 0.995) or 1
    for g in games:
        star = clamp(g.pop("_star") / star_top) ** 0.8
        perf = clamp(g.pop("_perf") / perf_top)
        g["cats"] += [round(star, 3), round(perf, 3)]
        g["m"]["r1"] = g.pop("_r1") or None
        if g.pop("_starEst", False):
            g["m"]["starEst"] = True
        if star >= 0.99:
            g["tags"].append("Loaded rosters")
        if g["perf"]:
            g["m"]["perfPts"] = g["perf"][0][3]
        if perf >= 0.97 and g["perf"]:
            g["tags"].append("Monster game")
        if not g["m"]["r1"]:
            del g["m"]["r1"]
        g["m"] = drop_empty(g["m"])


def default_score(cats):
    ws = [c["weight"] for c in RUBRIC["categories"]]
    return 100 * sum(w * c for w, c in zip(ws, cats)) / sum(ws)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=0)
    args = ap.parse_args()

    teams = json.loads((CACHE / "teams.json").read_text())
    stars = StarIndex()
    all_games = []
    (OUT / "timelines").mkdir(parents=True, exist_ok=True)
    seasons = []
    timelines = {}
    for path in sorted(CACHE.glob("[0-9][0-9][0-9][0-9].json")):
        season = json.loads(path.read_text())
        games, tls = grade_season(season, stars)
        if not games:
            continue
        seasons.append(season["season"])
        all_games.extend(games)
        timelines[season["season"]] = tls
        print(f"{season['season']}: graded {len(games)} games")

    scale_star_categories(all_games)
    # Star lists and stat lines only load when someone opens a game, so they live with the
    # per-season score timelines instead of the main games file.
    for g in all_games:
        detail = timelines[g["season"]][str(g["id"])]
        stars, perf = g.pop("stars"), g.pop("perf")
        if stars["home"] or stars["away"]:
            detail["stars"] = stars
        if perf:
            detail["perf"] = perf
    for year, tls in timelines.items():
        (OUT / "timelines" / f"{year}.json").write_text(json.dumps(tls, separators=(",", ":")))
    all_games.sort(key=lambda g: (g["date"], g["id"]))
    (OUT / "games.json").write_text(json.dumps(all_games, separators=(",", ":")))

    used = {str(g[s]["id"]) for g in all_games for s in ("home", "away")}
    meta = {
        "sport": "cfb",
        "title": "College Football",
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "seasons": seasons,
        "rubric": RUBRIC,
        "teams": {
            tid: {k: teams[tid].get(k) for k in ("school", "mascot", "abbr", "color", "alt", "logo", "logoDark")}
            for tid in sorted(used) if tid in teams
        },
        "conferences": sorted({g[s]["conf"] for g in all_games for s in ("home", "away") if g[s].get("conf")}),
    }
    (OUT / "meta.json").write_text(json.dumps(meta, separators=(",", ":")))
    print(f"wrote {len(all_games)} games to {OUT.relative_to(ROOT)}")

    if args.top:
        ranked = sorted(all_games, key=lambda g: -default_score(g["cats"]))
        for i, g in enumerate(ranked[: args.top], 1):
            h, a = g["home"], g["away"]
            print(f"{i:>3}. {default_score(g['cats']):5.1f}  {g['season']} {a['name']} {a['pts']} @ {h['name']} {h['pts']}"
                  f"  [{', '.join(g['tags'])}] {g.get('notes', '')}")


if __name__ == "__main__":
    main()
