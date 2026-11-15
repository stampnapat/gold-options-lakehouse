"""COMEX gold option expiry calendar.

Rules (CME rulebook, COMEX gold options):
- Monthly (OG): the 4th last business day of the month before the contract month;
  if that is a Friday or the day before a holiday, the business day before.
- Friday weeklies OG1..OG5: the nth Friday of the contract month.
- Monday-Thursday weeklies: every such weekday in the contract month, except days
  that are a holiday or a monthly expiry.
- Underlying: gold futures trade Feb/Apr/Jun/Aug/Oct/Dec; an option month uses the
  first futures month on or after it (Jan options -> Feb futures).
"""

from __future__ import annotations

from datetime import date, timedelta

# Days with no CME settlement for metals. Extend each year.
CME_HOLIDAYS = frozenset(
    date.fromisoformat(d)
    for d in (
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
        "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
        "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
        "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
    )
)  # fmt: skip

MONTHS = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
FUTURES_MONTHS = (2, 4, 6, 8, 10, 12)
WEEKDAY_OF = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3}


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in CME_HOLIDAYS


def business_days_between(start: date, end: date) -> int:
    """Business days in (start, end]: settlements left from `start`'s settlement to expiry."""
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        n += is_business_day(d)
    return n


def parse_month(code: str) -> tuple[int, int]:
    """'NOV26' -> (2026, 11)."""
    return 2000 + int(code[3:]), MONTHS.index(code[:3]) + 1


def month_code(year: int, month: int) -> str:
    return f"{MONTHS[month - 1]}{year % 100:02d}"


def _days_of_month(year: int, month: int):
    d = date(year, month, 1)
    while d.month == month:
        yield d
        d += timedelta(days=1)


def monthly_expiry(option_month: str) -> date:
    year, month = parse_month(option_month)
    year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    business = [d for d in _days_of_month(year, month) if is_business_day(d)]
    expiry = business[-4]
    nxt = expiry + timedelta(days=1)
    if expiry.weekday() == 4 or (nxt.weekday() < 5 and nxt in CME_HOLIDAYS):
        expiry = business[-5]
    return expiry


def futures_month(option_month: str) -> str:
    year, month = parse_month(option_month)
    fut = next((m for m in FUTURES_MONTHS if m >= month), None)
    return month_code(year, fut) if fut else month_code(year + 1, FUTURES_MONTHS[0])


def underlying_for(expiry: date) -> str:
    """Futures month for an option expiring on `expiry`: that of the first monthly
    option expiring on or after it."""
    year, month = expiry.year, expiry.month
    for _ in range(3):
        month_c = month_code(year, month)
        if monthly_expiry(month_c) >= expiry:
            return futures_month(month_c)
        year, month = (year, month + 1) if month < 12 else (year + 1, 1)
    raise ValueError(f"no monthly option expires after {expiry}")


def weekly_expiries(product_code: str, contract_month: str) -> list[date]:
    """Candidate expiry dates for a weekly product block in one contract month."""
    year, month = parse_month(contract_month)
    days = list(_days_of_month(year, month))
    root, kind = product_code.split()
    if kind in WEEKDAY_OF:
        monthlies = {
            monthly_expiry(month_code(year, month)),
            monthly_expiry(month_code(year + (month == 12), month % 12 + 1)),
        }
        return [
            d
            for d in days
            if d.weekday() == WEEKDAY_OF[kind] and is_business_day(d) and d not in monthlies
        ]
    if root.startswith("OG") and root[2:].isdigit():  # OG1..OG5: nth Friday
        fridays = [d for d in days if d.weekday() == 4]
        n = int(root[2:])
        return [fridays[n - 1]] if n <= len(fridays) else []
    return []
