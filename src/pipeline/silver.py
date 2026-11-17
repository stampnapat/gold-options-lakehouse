"""SILVER: one clean row per option series and trade date.

- Picks one bulletin per trade date: FINAL beats PRELIMINARY, then the latest.
- Fills side, expiry and underlying with cme_bulletin.infer (printed for monthly and
  Friday options, modeled with Black-76 for the Monday-Thursday weeklies).

A materialized view rather than a streaming table: inference needs a whole
bulletin at once (it reads the forward curve and smile from the monthly options),
and a full recompute keeps every row consistent when the inference improves.
At ~1 second per bulletin, a year of history recomputes in a few minutes.
"""

from dataclasses import fields

from pyspark import pipelines as dp
from pyspark.sql import Window
from pyspark.sql import functions as F

from cme_bulletin.infer import infer
from cme_bulletin.section64 import OptionRow

CATALOG = spark.conf.get("catalog")  # noqa: F821

ROW_FIELDS = [f.name for f in fields(OptionRow)]
OUT_SCHEMA = (
    "trade_date DATE, status STRING, bulletin_number INT, source_file STRING, "
    "product_code STRING, product_desc STRING, contract_month STRING, "
    "side STRING, expiry DATE, underlying STRING, forward DOUBLE, strike DOUBLE, "
    "settle DOUBLE, settle_chg DOUBLE, is_new BOOLEAN, delta DOUBLE, "
    "volume INT, open_interest INT, oi_chg INT, implied_vol DOUBLE, "
    "delta_error DOUBLE, score_margin DOUBLE, inference_method STRING"
)


def _infer_bulletin(pdf):
    """applyInPandas over one bulletin's rows."""
    pdf = pdf.sort_values(["page", "line_no"]).reset_index(drop=True)
    clean = pdf.astype(object).where(pdf.notna(), None)
    rows = [OptionRow(**{f: rec[f] for f in ROW_FIELDS}) for rec in clean.to_dict("records")]
    result = infer(rows, pdf["trade_date"].iloc[0])

    out = pdf[["trade_date", "status", "bulletin_number", "source_file", "product_code",
               "product_desc", "contract_month", "strike", "settle", "settle_chg", "is_new",
               "delta", "open_interest", "oi_chg"]].copy()  # fmt: skip
    out["volume"] = pdf["outcry_vol"] + pdf["globex_vol"] + pdf["pnt_vol"]
    for col in ("side", "expiry", "underlying", "forward", "implied_vol", "delta_error",
                "score_margin"):  # fmt: skip
        out[col] = [getattr(x, col) for x in result]
    out["inference_method"] = [x.method for x in result]
    return out[[c.split()[0] for c in OUT_SCHEMA.split(", ")]]


@dp.materialized_view(
    name=f"{CATALOG}.silver.option_series",
    comment="One row per gold option series (side, expiry, strike) per trade date",
    cluster_by=["trade_date", "expiry"],
)
@dp.expect_all(
    {
        # Expired series (zero open interest) legitimately have no side, and
        # far-wing minimum-tick series no expiry; anything else is a miss.
        "side_known": "side IS NOT NULL OR inference_method = 'expired'",
        "expiry_known": "expiry IS NOT NULL OR inference_method = 'moneyness'",
        "expiry_not_before_trade_date": "expiry IS NULL OR expiry >= trade_date",
        "forward_known": "forward IS NOT NULL",
    }
)
def option_series():
    rows = spark.read.table(f"{CATALOG}.bronze.option_rows")  # noqa: F821
    best_file = (
        rows.select("trade_date", "status", "bulletin_number", "source_file")
        .distinct()
        .withColumn(
            "rank",
            F.row_number().over(
                Window.partitionBy("trade_date").orderBy(
                    (F.col("status") == "FINAL").desc(),
                    F.col("bulletin_number").desc(),
                    F.col("source_file").desc(),
                )
            ),
        )
        .where("rank = 1")
        .select("source_file")
    )
    return (
        rows.join(best_file, "source_file")
        .groupBy("source_file")
        .applyInPandas(_infer_bulletin, OUT_SCHEMA)
    )
