# The People's Count: live feed

Turns the results page from hand-updated to live in about ten minutes.

## Setup (once)
1. Make a new public GitHub repo (any name, e.g. `peoples-count`). Public matters: the raw file URL is what the page reads.
2. Upload this folder's contents to the repo root, keeping the `.github/workflows/` and `scraper/` folders.
3. In the repo, open Settings, then Actions, then General, and allow workflows to run with read and write permissions.
4. Open the Actions tab, pick "Pull Missouri results," and click "Run workflow" once by hand. It writes `results.json` at the repo root.
5. Copy the raw URL of that file. It looks like
   `https://raw.githubusercontent.com/YOURNAME/peoples-count/main/results.json`
6. In the WordPress block for the results page, set `DATA_URL` to that address and save. The page now polls every three minutes and says "Live from the state feed" in the top bar.

## If the first run fails
The SOS site only shows the contest tables after polls close at 7 p.m. If the run says "Parsed zero contests," the page's text pattern differs from what the scraper expects. Open enr.sos.mo.gov, copy one contest's text (the "N of M Precincts Reported" line and the rows under it), and the two patterns `HEAD` and `ROW` in `scraper/fetch_results.py` can be adjusted to match. Until then the WordPress page keeps showing whatever numbers are typed into it, so nothing is lost.

## County races (COMBAT, County Executive)
The state feed does not carry these. Edit `scraper/county.json` (COMBAT, County Executive, Legislature) and `scraper/judges.json` (retention yes/no) in the repo; the next run folds them in. Or type them straight into the WordPress block.

## Calling races
Never set `called` from the feed. When AP or the SOS calls a race, set `"called": "AP"` on that contest in `contests.json` or `county.json`. The page prints "Called by AP" and nothing before that.
