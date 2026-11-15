"""Expected dates are the ones printed in the bulletin's expiry table."""

from datetime import date

import pytest

from cme_bulletin import calendar as cal


@pytest.mark.parametrize(
    ("month", "expiry"),
    [
        ("DEC26", date(2026, 11, 24)),  # Thanksgiving week
        ("JAN27", date(2026, 12, 28)),
        ("FEB27", date(2027, 1, 26)),
        ("MAR27", date(2027, 2, 23)),
        ("APR27", date(2027, 3, 24)),  # 4th last day is the eve of Good Friday
    ],
)
def test_monthly_expiry(month, expiry):
    assert cal.monthly_expiry(month) == expiry


def test_futures_month():
    assert [cal.futures_month(m) for m in ("DEC26", "JAN27", "FEB27", "MAR27")] == [
        "DEC26",
        "FEB27",
        "FEB27",
        "APR27",
    ]


def test_weekly_expiries_skip_the_monthly_expiry():
    assert cal.weekly_expiries("GWT TUE", "NOV26")[-1] == date(2026, 11, 17)  # 24th is OG Dec
    assert cal.weekly_expiries("GWW WED", "NOV26")[-1] == date(2026, 11, 25)


def test_friday_weeklies_are_nth_friday():
    assert cal.weekly_expiries("OG2 PUT", "NOV26") == [date(2026, 11, 13)]
    assert cal.weekly_expiries("OG1 CALL", "DEC26") == [date(2026, 12, 4)]


def test_underlying_switches_after_the_monthly_expiry():
    assert cal.underlying_for(date(2026, 11, 18)) == "DEC26"
    assert cal.underlying_for(date(2026, 11, 25)) == "FEB27"


def test_business_days_skip_weekends_and_holidays():
    assert cal.business_days_between(date(2026, 11, 20), date(2026, 11, 27)) == 4  # Thanksgiving
