# Greatest Games of All Time

A static website that grades sports games on a fixed rubric and lets you filter and sort them by season, week, team, conference, stage, grade and more. It starts with college football (2004–present, from the [CFBD API](https://collegefootballdata.com)).

## How games are graded

Each game gets six category scores from 0 to 1. The site combines them using the weights in [`config/rubric.json`](config/rubric.json), and visitors can adjust the weights with sliders.

| Category | Default weight | What it measures |
|---|---|---|
| Competitiveness | 25 | Final margin, and the share of the game played within one score |
| Drama | 25 | Lead changes and ties (extra credit in the last 5 minutes or OT), the biggest deficit the winner overcame, CFBD excitement index (2014+) |
| Finish | 20 | Overtime, and how late the winning lead was taken (walk-offs score highest) |
| Stakes | 15 | AP rankings of both teams, rivalries, conference title games, bowls, playoff |
| Upset | 10 | The winner's pregame win chance, from the betting spread (2013+) or Elo ratings |
| Fireworks | 5 | Combined points, with a bump when both teams score 35+ |

The total is a **score from 0 to 100**. **Letter grades are curved** across every game in the database: A+ is the top 1%, A the top 3%, B the top 18%, C the top 50%, and so on (see `grades` in the rubric file).

The detailed formulas are in [`pipeline/grade_cfb.py`](pipeline/grade_cfb.py).

## Layout

```
config/rubric.json        category weights and grade curve
pipeline/fetch_cfb.py     downloads from CFBD into data/cfb/ (cached per season)
pipeline/grade_cfb.py     grades games and writes site/data/cfb/
data/cfb/                 cached API data (committed, so old seasons are never re-fetched)
site/                     the website (plain HTML/CSS/JS, no build step)
.github/workflows/        twice-weekly refresh during the season
```

## Running it locally

```bash
pip install -r requirements.txt
export CFBD_API_KEY=...            # your CFBD key
python pipeline/fetch_cfb.py 2004-2026
python pipeline/grade_cfb.py --top 25
python -m http.server -d site 8000 # then open http://localhost:8000
```

`fetch_cfb.py` uses about 20 API calls per season (games, lines, polls, plus one play-by-play call per week). Finished seasons are cached and skipped, so a weekly refresh costs only a handful of calls.

## Deploying on Cloudflare Pages

1. Cloudflare dashboard → **Workers & Pages** → **Create** → **Pages** → **Connect to Git**, and pick this repo.
2. Build command: *(leave empty)*. Build output directory: `site`.
3. Add your custom domain under the project's **Custom domains** tab.

## Automatic refresh

The GitHub Action in `.github/workflows/refresh-cfb.yml` runs on Sundays and Wednesdays from August through January. It fetches new games, re-grades and commits, which triggers a Cloudflare redeploy. It needs a repository secret named `CFBD_API_KEY` (**Settings → Secrets and variables → Actions**). You can also run it by hand from the **Actions** tab.
