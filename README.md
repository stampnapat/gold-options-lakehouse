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

Bulletin data belongs to CME Group. **No bulletin PDF or data derived from one is
committed to this repository** (`*.pdf` and `data/` are git-ignored); it lives only in
the author's Databricks workspace. Tests use small synthetic fixtures.

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
3. Run the pipeline: `databricks bundle run gold_options -t dev`. Auto Loader
   ingests only files it has not seen before, so re-running is safe.

## Pipeline

| Table | What it holds |
|---|---|
| `bronze.bulletin_files` | one row per PDF (Auto Loader, `binaryFile`) |
| `bronze.option_rows` | gold option strike rows exactly as printed |
| `bronze.block_totals` | bulletin TOTAL lines vs. the sum of parsed rows (expectation: equal) |
| `bronze.parse_rejects` | gold lines the parser could not read (should stay empty) |

The parser (`src/cme_bulletin`) is built into a wheel by the bundle and installed in
the serverless pipeline environment, so Spark workers can import it.
