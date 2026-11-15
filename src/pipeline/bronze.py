"""BRONZE: bulletin PDFs from the landing volume, parsed into rows.

1. `bulletin_files`: Auto Loader (binaryFile) picks up each new PDF exactly once.
2. `option_rows`, `block_totals`, `parse_rejects`: every file is parsed with
   cme_bulletin.section64. Rows stay exactly as printed (no put/call or expiry
   inference yet); that belongs in silver.

Bronze keeps everything, including FINAL and PRELIMINARY copies of the same
trade date; silver decides which one wins.
"""

from pyspark import pipelines as dp
from pyspark.sql import functions as F

import cme_bulletin.section64 as s64

CATALOG = spark.conf.get("catalog")  # noqa: F821 (spark is injected by the pipeline)
LANDING_PATH = spark.conf.get("landing_path")  # noqa: F821

FILE_KEYS = "source_file STRING, trade_date DATE, status STRING, bulletin_number INT"

ROW_SCHEMA = (
    f"{FILE_KEYS}, product_code STRING, product_desc STRING, contract_month STRING, "
    "side STRING, strike DOUBLE, settle DOUBLE, settle_chg DOUBLE, is_new BOOLEAN, "
    "delta DOUBLE, exercises INT, outcry_vol INT, globex_vol INT, pnt_vol INT, "
    "open_interest INT, oi_chg INT, page INT, line_no INT"
)
TOTAL_SCHEMA = (
    f"{FILE_KEYS}, product_code STRING, contract_month STRING, "
    "open_interest INT, rows_open_interest INT"
)
REJECT_SCHEMA = f"{FILE_KEYS}, page INT, line_no INT, text STRING"


def _parse_files(kind: str, schema: str):
    """mapInPandas function: one bulletin file in, its rows/totals/rejects out."""
    # Explicit columns so a file with no rejects still yields a well-formed frame.
    columns = [c.strip().split()[0] for c in schema.split(",")]

    def run(batches):
        from dataclasses import asdict

        import pandas as pd

        for batch in batches:
            out = []
            for path, content in zip(batch["path"], batch["content"], strict=True):
                res = s64.parse_pdf(bytes(content))
                keys = {
                    "source_file": path,
                    "trade_date": res.trade_date,
                    "status": res.status,
                    "bulletin_number": res.bulletin_number,
                }
                if kind == "rows":
                    out += [keys | asdict(r) for r in res.rows]
                elif kind == "totals":
                    out += [keys | asdict(t) for t in res.totals]
                else:
                    out += [keys | {"page": p, "line_no": n, "text": t} for p, n, t in res.rejects]
            yield pd.DataFrame(out, columns=columns)

    return run


@dp.table(
    name=f"{CATALOG}.bronze.bulletin_files",
    comment="One row per uploaded Section 64 PDF, ingested once by Auto Loader",
)
def bulletin_files():
    return (
        spark.readStream.format("cloudFiles")  # noqa: F821
        .option("cloudFiles.format", "binaryFile")
        .option("pathGlobFilter", "*.pdf")
        .load(LANDING_PATH)
        .select("path", "modificationTime", "length", "content")
        .withColumn("_ingested_at", F.current_timestamp())
    )


def _parsed(kind: str, schema: str):
    return (
        spark.readStream.table(f"{CATALOG}.bronze.bulletin_files")  # noqa: F821
        .select("path", "content")
        .mapInPandas(_parse_files(kind, schema), schema)
        .withColumn("_ingested_at", F.current_timestamp())
    )


@dp.table(
    name=f"{CATALOG}.bronze.option_rows",
    comment="Gold option strike rows exactly as printed in the bulletin",
    cluster_by=["trade_date", "product_code"],
)
@dp.expect_all(
    {
        "has_trade_date": "trade_date IS NOT NULL",
        "strike_positive": "strike > 0",
        "oi_not_negative": "open_interest >= 0",
    }
)
def option_rows():
    return _parsed("rows", ROW_SCHEMA)


@dp.table(
    name=f"{CATALOG}.bronze.block_totals",
    comment="Bulletin TOTAL lines next to the sum of parsed rows, per product and month",
)
@dp.expect("rows_reconcile_with_total", "open_interest = rows_open_interest")
def block_totals():
    return _parsed("totals", TOTAL_SCHEMA)


@dp.table(
    name=f"{CATALOG}.bronze.parse_rejects",
    comment="Gold-product lines the parser could not read; should stay empty",
)
def parse_rejects():
    return _parsed("rejects", REJECT_SCHEMA)
