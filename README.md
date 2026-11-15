# Gold Options Lakehouse

A Databricks lakehouse for **COMEX gold daily-expiry options** (the weekly
Monday–Thursday and Friday contracts). It turns the CME Daily Bulletin into
strike-level tables, then computes the levels an options-flow trader watches:
SD bands, open-interest walls, max pain and gamma exposure, and backtests how
often price actually respects them.

> Status: work in progress.

## Data source and licensing

The only input is **CME Group Daily Bulletin, Section 64 (Metals Option Products)**,
downloaded by hand from <https://www.cmegroup.com/market-data/daily-bulletin.html>
once per trade date. CME's website terms forbid automated scraping, so this project
never fetches it with a script.

Bulletin data belongs to CME Group. **No bulletin PDF or parsed data set is committed
to this repository** (`*.pdf` and `data/` are git-ignored); it lives only in the
author's Databricks workspace. Tests use synthetic bulletins generated from Black-76.
The README quotes a few aggregate figures used to validate the pipeline.

## Stack

Databricks Free Edition (serverless), Unity Catalog, Lakeflow Declarative Pipelines,
Databricks Asset Bundles, Python with `pdfplumber`, pytest, GitHub Actions.

## Setup

1. Log in: `databricks auth login --host https://dbc-abd11c35-2b32.cloud.databricks.com`
2. Create the catalog `gold_options_dev` once in the UI (**Catalog → + → Create a
   catalog**, storage: *Default Storage*). Bundles manage schemas, not catalogs, and on
   Free Edition the API cannot create a catalog on Default Storage.
3. Create a local environment: `uv venv --python 3.12 && uv pip install -e ".[dev]"`
4. Deploy schemas, the landing volume and the pipeline: `databricks bundle deploy -t dev`

## Daily routine

1. Download *Metals Option Products - PG 64* from the Daily Bulletin page (the page
   keeps only the previous trade date, so a missed day is lost).
2. Upload it; the file is renamed from its own header to `<trade_date>_<FINAL|PRELIMINARY>.pdf`:
   `.venv/bin/python scripts/upload_bulletin.py ~/Downloads/Section64_*.pdf`
3. Nothing else: the job `gold_options_refresh_dev` has a **file-arrival trigger** on
   the landing folder and refreshes the pipeline within about a minute of the upload.
   Auto Loader ingests only files it has not seen before, so a manual
   `databricks bundle run gold_options -t dev` is always safe too.

## CI/CD

`.github/workflows/ci.yml`: every push and pull request runs ruff and pytest; a push
to `main` then validates and deploys the bundle (`dev` target) with the
`DATABRICKS_HOST` and `DATABRICKS_TOKEN` repository secrets.

## Pipeline

| Table | What it holds |
|---|---|
| `bronze.bulletin_files` | one row per PDF (Auto Loader, `binaryFile`) |
| `bronze.option_rows` | gold option strike rows exactly as printed |
| `bronze.block_totals` | bulletin TOTAL lines vs. the sum of parsed rows (expectation: equal) |
| `bronze.parse_rejects` | gold lines the parser could not read (should stay empty) |
| `silver.option_series` | one row per series (side, expiry, strike) per trade date, FINAL bulletin preferred |
| `gold.strike_profile` | calls and puts side by side per strike for every live expiry |
| `gold.expiry_levels` | per trade date and expiry: forward, ATM vol, 1-3 SD bands, put/call walls inside 2 SD, max pain, GEX, gamma flip; `expiry_rank = 1` is the next day's daily option |

Levels are computed by `src/options_math/levels.py` (pure Python, unit tested).
Vols are annualized on business days / 252; SD bands are lognormal around the
forward. GEX assumes dealers are long calls and short puts (the common public
convention), so it shows positioning, not a known dealer book.

**After changing parser or inference code**, recompute the views; a materialized
view whose source tables did not change is otherwise left as it was:

    databricks bundle run gold_options -t dev --full-refresh \
      gold_options_dev.silver.option_series,gold_options_dev.gold.strike_profile,gold_options_dev.gold.expiry_levels

### Inferring side and expiry

The bulletin prints the Monday-Thursday weekly options under one header per
month with no put/call or expiry column: one strike can have six rows (three
expiries times two sides). `src/cme_bulletin/infer.py` recovers them with
Black-76:

1. The monthly options print their side, so each near-the-money row gives the
   futures price; the median per futures month is the forward curve.
2. The expiry calendar (`calendar.py`, checked against the bulletin's own expiry
   table) lists the block's candidate expiries and the futures each one is on.
3. Each row is scored under every (side, expiry): with the forward fixed, the
   premium gives a volatility, the model delta must match the printed delta, and
   the volatility must sit near the monthly smile. Each (side, expiry) is used once
   per strike.

Check against an independent source: for the Wednesday 2026-11-11 expiry, open
interest summed over strikes 3900-4185 comes out at **653 calls / 1,157 puts**,
exactly what a third-party CME open-interest monitor showed for that contract. On
synthetic bulletins generated from Black-76 and shuffled, the tests recover every
row. Far-out-of-the-money rows quoted at the minimum tick with no delta get a side
(from the strike) but no expiry, and are flagged `inference_method = 'moneyness'`.

The parser (`src/cme_bulletin`) is built into a wheel by the bundle and installed in
the serverless pipeline environment, so Spark workers can import it.
