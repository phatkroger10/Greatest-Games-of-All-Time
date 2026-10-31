"""Star Power and Star Performance measurements for college football games.

Star Power: how much talent was on the field. Each player on either team gets a value
(the highest that applies), and a team's total is the sum:
  - NFL draft pick: round 1 = 1.0, round 2 = 0.55, round 3 = 0.35, later = 0.15
  - hand-picked star from config/stars.json (Heisman winners etc.) = 1.25
  - top-3 nationally that season in passing, rushing or receiving yards, total TDs or sacks = 0.6
A drafted player counts for games 1-4 seasons before his draft. If a roster or box score
confirms he was on the team that season he counts in full; otherwise his value is scaled by
how likely he was already contributing (1.0 / 0.85 / 0.6 / 0.3 for 1-4 years out).
Recent seasons whose players haven't all been drafted yet are scaled up to compensate.

Star Performance: the best individual stat line in the game (fantasy-style points from the box
score), weighted by the setting: big games and ranked opponents count more, as does winning.
"""

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cfb"
STARS = json.loads((ROOT / "config" / "stars.json").read_text())

ROUND_VALUE = {1: 1.0, 2: 0.55, 3: 0.35}
LATER_ROUND_VALUE = 0.15
WINDOW = {1: 1.0, 2: 0.85, 3: 0.6, 4: 0.3}  # years between the game's season and the draft
# Rough share of a team's eventual draft value that comes from each draft after a season.
EXPECTED_SHARE = {1: 0.45, 2: 0.3, 3: 0.18, 4: 0.07}
LEADER_VALUE = 0.6
LEADER_STATS = [("passYds", "passing yards"), ("rushYds", "rushing yards"), ("recYds", "receiving yards"),
                ("tds", "total touchdowns"), ("sacks", "sacks")]


def ordinal(n):
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")


class StarIndex:
    def __init__(self):
        path = CACHE / "draft.json"
        draft = json.loads(path.read_text()) if path.exists() else {"years": [], "picks": {}}
        self.draft_years = set(draft["years"])
        self.picks_by_team = defaultdict(list)  # team -> [(id, name, year, round)]
        for aid, (name, team, year, rnd, _overall, _pos) in draft["picks"].items():
            self.picks_by_team[team].append((aid, name, year, rnd))
        self.seasons = {}
        self.team_cache = {}

    def season(self, year):
        if year not in self.seasons:
            path = CACHE / "players" / f"{year}.json"
            self.seasons[year] = json.loads(path.read_text()) if path.exists() else None
        return self.seasons[year]

    def has_data(self, year):
        return self.season(year) is not None

    # ------------------------------------------------------------ star power

    def _leaders(self, year):
        data = self.season(year)
        out = {}
        if not data:
            return out
        totals = data["totals"]
        for key, label in LEADER_STATS:
            if key == "tds":
                val = lambda t: t.get("passTD", 0) + t.get("rushTD", 0) + t.get("recTD", 0)  # noqa: E731
            else:
                val = lambda t, k=key: t.get(k, 0)  # noqa: E731
            ranked = sorted(totals.items(), key=lambda kv: -val(kv[1]))[:3]
            for i, (aid, t) in enumerate(ranked, 1):
                if val(t) > 0 and aid not in out:
                    out[aid] = f"#{i} nationally in {label}"
        return out

    def _draft_coverage(self, year):
        return sum(EXPECTED_SHARE[k] for k in EXPECTED_SHARE if (year + k) in self.draft_years)

    def _draft_part(self, team, year):
        """Draft-based stars for a team-season: {id: (value, name, label)} before any scaling."""
        data = self.season(year)
        roster = set((data or {}).get("roster", {}).get(team, []))
        totals = (data or {}).get("totals", {})
        names = (data or {}).get("names", {})
        # Older drafts use different player ids than the box scores, so fall back to name + team.
        by_name = {names.get(a): a for a, t in totals.items() if t.get("team") == team}
        out = {}
        for draft_id, name, dyear, rnd in self.picks_by_team.get(team, []):
            gap = dyear - year
            if gap not in WINDOW:
                continue
            aid = draft_id if (draft_id in totals or draft_id in roster) else by_name.get(name, draft_id)
            seen = totals.get(aid)
            if seen and seen.get("team") != team:
                continue  # played somewhere else that season (transfer)
            confirmed = aid in roster or bool(seen)
            value = ROUND_VALUE.get(rnd, LATER_ROUND_VALUE) * (1.0 if confirmed else WINDOW[gap])
            out[aid] = (value, name, f"{ordinal(rnd)}-round pick ({dyear})")
        return out

    def team_stars(self, team, year):
        """All stars for a team-season: (total value, [(value, name, label)], estimated?)."""
        key = (team, year)
        if key in self.team_cache:
            return self.team_cache[key]
        data = self.season(year)
        names = (data or {}).get("names", {})
        totals = (data or {}).get("totals", {})

        draft = self._draft_part(team, year)
        coverage = self._draft_coverage(year)
        estimated = coverage < 0.999
        if coverage == 0 and self.has_data(year - 1):
            # No draft has happened since this season: borrow last season's draft talent.
            prev = self._draft_part(team, year - 1)
            prev_cov = self._draft_coverage(year - 1) or 1
            draft_total = 0.9 * sum(v for v, _, _ in prev.values()) / prev_cov
        else:
            draft_total = sum(v for v, _, _ in draft.values()) / max(coverage, 0.33)

        stars = {aid: entry for aid, entry in draft.items()}
        bonus = 0.0
        # Hand-picked stars.
        for s in STARS["players"]:
            if s["team"] != team or year not in s["seasons"]:
                continue
            aid = next((a for a, n in names.items() if n == s["name"] and totals.get(a, {}).get("team") == team), None)
            aid = aid or f"star:{s['name']}"
            prev = stars.get(aid, (0, s["name"], ""))[0]
            bonus += max(0.0, STARS["value"] - prev)
            stars[aid] = (max(prev, STARS["value"]), s["name"], s["note"])
        # National stat leaders.
        for aid, label in self._leaders(year).items():
            if totals.get(aid, {}).get("team") != team:
                continue
            prev = stars.get(aid, (0, names.get(aid, "?"), ""))[0]
            if LEADER_VALUE > prev:
                bonus += LEADER_VALUE - prev
                stars[aid] = (LEADER_VALUE, names.get(aid, "?"), label)
            elif prev:  # already a draft pick or star: mention the stat title too
                v, n, lab = stars[aid]
                stars[aid] = (v, n, f"{lab}; {label}")

        total = draft_total + bonus
        top = sorted(stars.values(), key=lambda e: -e[0])
        result = (total, top, estimated)
        self.team_cache[key] = result
        return result

    # ------------------------------------------------------------ performances

    def performances(self, year, game_id):
        data = self.season(year)
        if not data:
            return []
        names = data["names"]
        return [(names.get(aid, "?"), team, pts, line) for aid, team, pts, line in data["box"].get(str(game_id), [])]


def star_power_raw(home_total, away_total):
    """Both teams loaded beats one loaded team against a cupcake."""
    lo, hi = sorted((home_total, away_total))
    return (2 * lo + hi) / 3
