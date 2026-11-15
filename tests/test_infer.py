"""Inference on a synthetic bulletin generated from Black-76 with known answers.

Rows are rounded like the bulletin (premium to 0.1, delta to 4 places) and the
weekly block is shuffled, so the test sees what the parser would hand over.
"""

import math
import random
from datetime import date

import pytest

from cme_bulletin import calendar as cal
from cme_bulletin.infer import infer
from cme_bulletin.section64 import OptionRow
from options_math import black76

TRADE_DATE = date(2026, 11, 9)
FORWARDS = {"DEC26": 4000.0, "FEB27": 4040.0}


def smile(strike, forward):
    return 0.24 + 0.4 * math.log(strike / forward) ** 2  # mild smile


def row(code, month, side, strike, expiry):
    forward = FORWARDS[cal.underlying_for(expiry)]
    t = cal.business_days_between(TRADE_DATE, expiry) / 252
    v = smile(strike, forward) * math.sqrt(t)
    return OptionRow(
        product_code=code, product_desc="synthetic", contract_month=month,
        side=side if code.startswith("OG") else None, strike=float(strike),
        settle=round(black76.price(side, forward, strike, v), 1), settle_chg=0.0,
        is_new=False, delta=round(black76.delta(side, forward, strike, v), 4),
        exercises=0, outcry_vol=0, globex_vol=0, pnt_vol=0, open_interest=10,
        oi_chg=0, page=1, line_no=0,
    )  # fmt: skip


@pytest.fixture(scope="module")
def bulletin():
    monthly = [
        row(f"OG {name}", month, side, k, cal.monthly_expiry(month))
        for month in ("DEC26", "JAN27")
        for side, name in (("C", "CALL"), ("P", "PUT"))
        for k in range(3800, 4250, 25)
    ]
    truth, weekly = [], []
    for expiry in cal.weekly_expiries("GWW WED", "NOV26"):
        if expiry <= TRADE_DATE:
            continue
        for k in range(3900, 4150, 25):
            for side in "CP":
                weekly.append(row("GWW WED", "NOV26", side, k, expiry))
                truth.append((side, expiry))
    order = list(range(len(weekly)))
    random.Random(7).shuffle(order)
    return monthly + [weekly[i] for i in order], [truth[i] for i in order], len(monthly)


def test_recovers_side_and_expiry_of_weekly_rows(bulletin):
    rows, truth, n_monthly = bulletin
    inferred = infer(rows, TRADE_DATE)[n_monthly:]
    got = [(x.side, x.expiry) for x in inferred]
    assert got == truth
    assert all(x.method == "model" for x in inferred)


def test_reads_forward_curve_and_underlying(bulletin):
    rows, truth, n_monthly = bulletin
    inferred = infer(rows, TRADE_DATE)
    pairs = zip(inferred[n_monthly:], truth, strict=True)
    late = next(x for x, t in pairs if t[1] == date(2026, 11, 25))
    assert late.underlying == "FEB27"
    assert late.forward == pytest.approx(4040, abs=1)


def test_printed_side_keeps_side_and_gets_calendar_expiry(bulletin):
    rows, _, _ = bulletin
    first = infer(rows, TRADE_DATE)[0]
    assert (first.method, first.side, first.expiry) == ("printed", "C", date(2026, 11, 24))


def test_far_wing_with_min_tick_and_no_delta_gets_side_only(bulletin):
    rows, _, _ = bulletin
    wing = row("GWW WED", "NOV26", "P", 3000, date(2026, 11, 11))
    wing = OptionRow(**{**wing.__dict__, "settle": 0.1, "delta": None})
    result = infer(rows + [wing], TRADE_DATE)[-1]
    assert (result.method, result.side, result.expiry) == ("moneyness", "P", None)
