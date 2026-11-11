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
2. Create the catalog once (bundles manage schemas, not catalogs):
   `databricks catalogs create gold_options_dev`
3. Deploy schemas and the landing volume: `databricks bundle deploy -t dev`
