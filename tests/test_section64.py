"""Parser tests on synthetic bulletin lines (made-up numbers, same layout as CME's)."""

from datetime import date

import pytest

from cme_bulletin.section64 import parse_lines, parse_strike_row

HEADER = [
    "PG64 BULLETIN # 101@ METALS OPTION PRODUCTS Tue, Mar 03, 2026 PG64",
    "FINAL",
]
EMPTY = "---- ---- ---- ---- ----"


def lines(*texts, page=1):
    return [(page, i, t) for i, t in enumerate(texts, start=1)]


def test_bulletin_header():
    res = parse_lines(lines(*HEADER))
    assert res.trade_date == date(2026, 3, 3)
    assert res.bulletin_number == 101
    assert res.status == "FINAL"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            f"3000 {EMPTY} 12.50 - 1.25 .4100 ---- ---- 7 ---- 20 + 3",
            {
                "strike": 3000.0,
                "settle": 12.5,
                "settle_chg": -1.25,
                "is_new": False,
                "delta": 0.41,
                "globex_vol": 7,
                "open_interest": 20,
                "oi_chg": 3,
            },
        ),
        (  # unchanged settlement and open interest
            f"3050 {EMPTY} 8.00 UNCH .3000 ---- ---- ---- ---- 5 UNCH",
            {"settle_chg": 0.0, "globex_vol": 0, "oi_chg": 0},
        ),
        (  # strike listed today: no settlement change, all OI is new
            f"3100 {EMPTY} 4.10 NEW .2000 ---- ---- 9 ---- 9 NEW",
            {"settle_chg": None, "is_new": True, "oi_chg": 9},
        ),
        (  # sign glued to the number, thousands separators
            "3150 1.10 1.10 2.00B/1.00A 2.00B/1.00A ---- "
            "1,234.50 +10.00 .9900 ---- 2 1,500 ---- 2,000 -45",
            {
                "settle": 1234.5,
                "settle_chg": 10.0,
                "outcry_vol": 2,
                "globex_vol": 1500,
                "open_interest": 2000,
                "oi_chg": -45,
            },
        ),
    ],
)
def test_parse_strike_row(text, expected):
    row = parse_strike_row(text)
    assert row is not None
    for key, value in expected.items():
        assert row[key] == value, key


def test_rejects_unparseable_row():
    assert parse_strike_row("3000 ---- garbled line") is None


def test_weekly_block_keeps_rows_in_order_without_side():
    res = parse_lines(
        lines(
            *HEADER,
            "GWW WED GOLD WEEKLY WEDNESDAY OPTION WEEK2",
            "MAR26",
            f"3000 {EMPTY} 0.30 - 0.10 .0100 ---- ---- 1 ---- 4 UNCH",
            f"3000 {EMPTY} 0.90 - 0.20 .0300 ---- ---- ---- ---- 6 + 2",
            "TOTAL 1 10+ 2",
        )
    )
    assert [r.settle for r in res.rows] == [0.3, 0.9]
    assert all(r.side is None and r.contract_month == "MAR26" for r in res.rows)
    assert res.totals[0].open_interest == 10
    assert res.mismatched_totals == []


def test_side_comes_from_header():
    res = parse_lines(
        lines(
            "OG PUT COMEX GOLD OPTIONS",
            "APR26",
            f"3000 {EMPTY} 5.00 UNCH .2000 ---- ---- ---- ---- 1 UNCH",
            "OG CALL COMEX GOLD OPTIONS",
            "APR26",
            f"3000 {EMPTY} 9.00 UNCH .8000 ---- ---- ---- ---- 2 UNCH",
        )
    )
    assert [(r.product_code, r.side) for r in res.rows] == [("OG PUT", "P"), ("OG CALL", "C")]


def test_block_continues_across_pages_and_description_changes():
    page1 = lines(
        "GWR THU GOLD WEEKLY THURSDAY OPTION Week3",
        "MAR26",
        f"3000 {EMPTY} 1.00 UNCH .1000 ---- ---- ---- ---- 3 UNCH",
        "64 METALS OPTIONS PRODUCTS 64",  # page footer must not become a row
    )
    page2 = lines(
        "GWR THU GOLD WEEKLY THURSDAY OPTION Week3",  # header repeated, no month line
        f"3050 {EMPTY} 2.00 UNCH .2000 ---- ---- ---- ---- 4 UNCH",
        "GWR THU GOLD WEEKLY THURSDAY OPTION Week2",  # same block, new description
        f"3100 {EMPTY} 3.00 UNCH .3000 ---- ---- ---- ---- 5 UNCH",
        "TOTAL 12 0",
        page=2,
    )
    res = parse_lines(page1 + page2)
    assert [r.strike for r in res.rows] == [3000, 3050, 3100]
    assert [r.product_desc[-5:] for r in res.rows] == ["Week3", "Week3", "Week2"]
    assert res.totals[0].rows_open_interest == 12
    assert res.mismatched_totals == []
    assert res.rejects == []


def test_total_mismatch_is_reported():
    res = parse_lines(
        lines(
            "OG1 PUT GOLD OPTIONS",
            "MAR26",
            f"3000 {EMPTY} 1.00 UNCH .1000 ---- ---- ---- ---- 3 UNCH",
            "TOTAL 0 99 0",
        )
    )
    assert len(res.mismatched_totals) == 1


def test_non_gold_products_are_skipped_and_bad_gold_rows_rejected():
    res = parse_lines(
        lines(
            "SO CALL COMEX SILVER OPTIONS",
            "MAR26",
            "3000 this silver line would not parse",
            "OMG OPT MICRO GOLD OPTIONS",
            "MAR26",
            f"3000 {EMPTY} 1.00 UNCH .1000 ---- ---- ---- ---- 3 UNCH",
            "OG2 CALL GOLD OPTIONS",
            "MAR26",
            "3000 ---- garbled",
        )
    )
    assert res.rows == []
    assert [t for _, _, t in res.rejects] == ["3000 ---- garbled"]
