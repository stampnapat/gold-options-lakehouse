"""GOLD: per-strike profile and per-expiry levels for every live expiry.

`expiry_rank = 1` is the next expiry after the bulletin's trade date: for a Monday
bulletin that is Tuesday's daily option, the contract traded "today" on Tuesday.
"""

from pyspark import pipelines as dp
from pyspark.sql import Window
from pyspark.sql import functions as F

from cme_bulletin.calendar import business_days_between
from options_math.levels import Strike, expiry_levels

CATALOG = spark.conf.get("catalog")  # noqa: F821

LEVELS_SCHEMA = (
    "trade_date DATE, expiry DATE, expiry_rank INT, product_code STRING, underlying STRING, "
    "forward DOUBLE, business_days_to_expiry INT, atm_vol DOUBLE, "
    "sd1_low DOUBLE, sd1_high DOUBLE, sd2_low DOUBLE, sd2_high DOUBLE, "
    "sd3_low DOUBLE, sd3_high DOUBLE, call_oi INT, put_oi INT, put_call_oi_ratio DOUBLE, "
    "max_pain DOUBLE, call_wall DOUBLE, call_wall_oi INT, put_wall DOUBLE, put_wall_oi INT, "
    "gex DOUBLE, gamma_flip DOUBLE"
)
LEVEL_COLUMNS = [c.split()[0] for c in LEVELS_SCHEMA.split(", ")]


def _side(col: str, side: str):
    return F.max(F.when(F.col("side") == side, F.col(col)))


@dp.materialized_view(
    name=f"{CATALOG}.gold.strike_profile",
    comment="Calls and puts side by side per strike, for every live expiry",
    cluster_by=["trade_date", "expiry"],
)
def strike_profile():
    series = spark.read.table(f"{CATALOG}.silver.option_series").where(  # noqa: F821
        "expiry > trade_date AND side IS NOT NULL"
    )
    by_strike = series.groupBy("trade_date", "expiry", "strike").agg(
        # "OG3 PUT" / "OG3 CALL" -> "OG3": this row covers both sides.
        F.first(F.regexp_replace("product_code", " (PUT|CALL)$", "")).alias("product_code"),
        F.first("underlying").alias("underlying"),
        F.first("forward").alias("forward"),
        F.coalesce(_side("open_interest", "C"), F.lit(0)).alias("call_oi"),
        F.coalesce(_side("open_interest", "P"), F.lit(0)).alias("put_oi"),
        _side("oi_chg", "C").alias("call_oi_chg"),
        _side("oi_chg", "P").alias("put_oi_chg"),
        _side("volume", "C").alias("call_volume"),
        _side("volume", "P").alias("put_volume"),
        _side("settle", "C").alias("call_settle"),
        _side("settle", "P").alias("put_settle"),
        _side("implied_vol", "C").alias("call_iv"),
        _side("implied_vol", "P").alias("put_iv"),
    )
    rank = Window.partitionBy("trade_date").orderBy("expiry")
    return by_strike.withColumn("expiry_rank", F.dense_rank().over(rank))


def _levels(pdf):
    """applyInPandas over one (trade_date, expiry)."""
    import pandas as pd

    first = pdf.iloc[0]
    clean = pdf.astype(object).where(pdf.notna(), None)
    strikes = [
        Strike(r["strike"], int(r["call_oi"]), int(r["put_oi"]), r["call_iv"], r["put_iv"])
        for r in clean.to_dict("records")
    ]
    days = business_days_between(first["trade_date"], first["expiry"])
    out = {
        "trade_date": first["trade_date"],
        "expiry": first["expiry"],
        "expiry_rank": int(first["expiry_rank"]),
        "product_code": first["product_code"],
        "underlying": first["underlying"],
        "forward": first["forward"],
        "business_days_to_expiry": days,
    }
    if first["forward"] is not None and pd.notna(first["forward"]):
        out |= expiry_levels(strikes, float(first["forward"]), days / 252)
    return pd.DataFrame([out]).reindex(columns=LEVEL_COLUMNS)


@dp.materialized_view(
    name=f"{CATALOG}.gold.expiry_levels",
    comment="SD bands, OI walls, max pain and gamma levels per trade date and expiry",
    cluster_by=["trade_date"],
)
@dp.expect_all(
    {
        "forward_known": "forward IS NOT NULL",
        "bands_ordered": "sd1_low IS NULL OR (sd1_low < forward AND forward < sd1_high)",
    }
)
def expiry_levels_view():
    return (
        spark.read.table(f"{CATALOG}.gold.strike_profile")  # noqa: F821
        .groupBy("trade_date", "expiry")
        .applyInPandas(_levels, LEVELS_SCHEMA)
    )
