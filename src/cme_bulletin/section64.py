"""Parse CME Daily Bulletin Section 64 (Metals Option Products) into strike rows.

The bulletin is a fixed-layout report. Each product block looks like:

    GWW WED          GOLD WEEKLY WEDNESDAY OPTION WEEK2      <- product header
    NOV26                                                    <- contract month
    4000 ---- ---- ... 13.20 -13.20 .0939 ---- ---- 67 ---- 249 + 59
    TOTAL 1640 15 5159 + 841                                 <- block total

Rows are parsed right-to-left: the right-hand columns (settlement onwards) are
always present and separated by spaces, while the open/high/low columns on the
left are sparse and sometimes run into each other. We only keep the right side.

Parsing is pure text so it can be tested without a PDF; `extract_lines` is the
only function that touches pdfplumber.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime

# Full-size gold options, keyed by the code at the start of the product header.
# Micro gold (OMG, MMG, WMG, FMG) is excluded: a different contract size.
GOLD_PRODUCTS = {
    "OG PUT",
    "OG CALL",  # monthly
    "OG1 PUT",
    "OG1 CALL",
    "OG2 PUT",
    "OG2 CALL",  # Friday weeklies
    "OG3 PUT",
    "OG3 CALL",
    "OG4 PUT",
    "OG4 CALL",
    "OG5 PUT",
    "OG5 CALL",
    "GMW MON",
    "GWT TUE",
    "GWW WED",
    "GWR THU",  # Monday-Thursday weeklies
}

_PRODUCT_HEADER = re.compile(
    r"^(?P<code>[A-Z0-9]{2,5} (?:PUT|CALL|MON|TUE|WED|THU|OPT|OOF))\s+(?P<desc>\S.*)$"
)
_CONTRACT_MONTH = re.compile(r"^(?P<month>[A-Z]{3}\d{2})$")
_TRADE_DATE = re.compile(
    r"PG64 BULLETIN # (?P<num>\d+).*?(?P<date>[A-Z][a-z]{2}, [A-Z][a-z]{2} \d{2}, \d{4})"
)
_STATUS = re.compile(r"^(?P<status>FINAL|PRELIMINARY)$")

_NUM = r"[\d,]*\.\d+"
_COUNT = r"[\d,]+|----"
_STRIKE_ROW = re.compile(
    rf"^(?P<strike>\d+(?:\.\d+)?)\s.*?"
    rf"(?P<settle>{_NUM}|----)\s+"
    rf"(?P<settle_chg>UNCH|NEW|[+-]\s?{_NUM})\s+"
    rf"(?P<delta>-?{_NUM}|----)\s+"
    rf"(?P<exercises>{_COUNT})\s+"
    rf"(?P<outcry_vol>{_COUNT})\s+"
    rf"(?P<globex_vol>{_COUNT})\s+"
    rf"(?P<pnt_vol>{_COUNT})\s+"
    rf"(?P<oi>[\d,]+)\s+"
    rf"(?P<oi_chg>UNCH|NEW|[+-]\s?[\d,]+)\s*$"
)
# A strike followed by a value or "----"; page furniture like "64 METALS ..." is not.
_ROW_START = re.compile(r"^\d+(?:\.\d+)?\s+(?:----|[\d.])")
_TOTAL_ROW = re.compile(r"^TOTAL\s+(?P<rest>.+)$")


@dataclass(frozen=True)
class OptionRow:
    """One strike line, in bulletin order. Put/call and expiry are NOT known yet
    for the Monday-Thursday weeklies: the bulletin mixes several weeks and both
    sides under one header, so `side` is only set when the header names it."""

    product_code: str
    product_desc: str
    contract_month: str
    side: str | None  # "P"/"C" when the header says PUT/CALL, else None
    strike: float
    settle: float | None
    settle_chg: float | None  # None when the strike is new today
    is_new: bool
    delta: float | None
    exercises: int
    outcry_vol: int
    globex_vol: int
    pnt_vol: int
    open_interest: int
    oi_chg: int
    page: int
    line_no: int  # position on the page, keeps the bulletin's row order


@dataclass(frozen=True)
class BlockTotal:
    product_code: str
    contract_month: str
    open_interest: int
    rows_open_interest: int  # sum of the block's rows; must equal open_interest


@dataclass
class ParseResult:
    trade_date: date | None = None
    bulletin_number: int | None = None
    status: str | None = None  # FINAL or PRELIMINARY
    rows: list[OptionRow] = field(default_factory=list)
    totals: list[BlockTotal] = field(default_factory=list)
    rejects: list[tuple[int, int, str]] = field(default_factory=list)  # (page, line, text)

    @property
    def mismatched_totals(self) -> list[BlockTotal]:
        return [t for t in self.totals if t.open_interest != t.rows_open_interest]


def _count(token: str) -> int:
    return 0 if token == "----" else int(token.replace(",", ""))


def _signed(token: str) -> float:
    return float(token.replace(" ", "").replace(",", ""))


def _change(token: str, *, unch, new):
    if token == "UNCH":
        return unch
    if token == "NEW":
        return new
    return _signed(token)


def _side(code: str) -> str | None:
    if code.endswith(" PUT"):
        return "P"
    if code.endswith(" CALL"):
        return "C"
    return None


def parse_strike_row(text: str) -> dict | None:
    """Parse the numeric fields of one strike line, or None if it does not match."""
    m = _STRIKE_ROW.match(text)
    if not m:
        return None
    g = m.groupdict()
    oi = _count(g["oi"])
    return {
        "strike": float(g["strike"]),
        "settle": None if g["settle"] == "----" else _signed(g["settle"]),
        "settle_chg": _change(g["settle_chg"], unch=0.0, new=None),
        "is_new": g["settle_chg"] == "NEW",
        "delta": None if g["delta"] == "----" else _signed(g["delta"]),
        "exercises": _count(g["exercises"]),
        "outcry_vol": _count(g["outcry_vol"]),
        "globex_vol": _count(g["globex_vol"]),
        "pnt_vol": _count(g["pnt_vol"]),
        "open_interest": oi,
        # NEW means the whole open interest appeared today.
        "oi_chg": int(_change(g["oi_chg"], unch=0, new=oi)),
    }


def _total_open_interest(rest: str) -> int:
    """`TOTAL <volumes...> <oi> <oi change>`. The change may be "+ 841", a bare 0,
    or glued to the open interest by the PDF text layer ("791+ 39")."""
    rest = re.sub(r"(\d)([+-])", r"\1 \2", rest)
    tokens = re.sub(r"([+-])\s+(\d)", r"\1\2", rest).split()
    return _count(tokens[-2])


def parse_lines(lines: Iterable[tuple[int, int, str]]) -> ParseResult:
    """Parse (page, line_no, text) tuples. Only GOLD_PRODUCTS rows are kept;
    a gold line that cannot be parsed is recorded in `rejects`, never dropped."""
    result = ParseResult()
    code = desc = month = None
    block_oi = 0

    for page, line_no, raw in lines:
        text = raw.strip()

        if result.trade_date is None and (m := _TRADE_DATE.search(text)):
            result.trade_date = datetime.strptime(m["date"], "%a, %b %d, %Y").date()
            result.bulletin_number = int(m["num"])
            continue
        if result.status is None and (m := _STATUS.match(text)):
            result.status = m["status"]
            continue
        if m := _PRODUCT_HEADER.match(text):
            # Every page repeats the product header; a block that continues onto
            # the next page keeps its contract month unless a new one is printed.
            # The description can change inside one block ("...Week3" then
            # "...Week2") while the TOTAL still covers the whole code + month.
            if m["code"] != code:
                code, month = m["code"], None
            desc = m["desc"].strip()
            continue
        if code not in GOLD_PRODUCTS:
            continue
        if m := _CONTRACT_MONTH.match(text):
            if m["month"] != month:
                month, block_oi = m["month"], 0
            continue
        if m := _TOTAL_ROW.match(text):
            if month is not None:
                result.totals.append(
                    BlockTotal(code, month, _total_open_interest(m["rest"]), block_oi)
                )
            continue
        if not _ROW_START.match(text):
            continue  # column headers, footers, page furniture
        if month is None:
            result.rejects.append((page, line_no, text))  # row before any contract month
            continue

        fields = parse_strike_row(text)
        if fields is None:
            result.rejects.append((page, line_no, text))
            continue
        block_oi += fields["open_interest"]
        result.rows.append(
            OptionRow(
                product_code=code,
                product_desc=desc,
                contract_month=month,
                side=_side(code),
                page=page,
                line_no=line_no,
                **fields,
            )
        )
    return result


def extract_lines(pdf, max_pages: int | None = None) -> list[tuple[int, int, str]]:
    """Text lines of a bulletin PDF (path, file object or bytes) as (page, line_no, text)."""
    import io

    import pdfplumber

    source = io.BytesIO(pdf) if isinstance(pdf, bytes | bytearray) else pdf
    out = []
    with pdfplumber.open(source) as doc:
        for page_no, page in enumerate(doc.pages[:max_pages], start=1):
            for line_no, text in enumerate((page.extract_text() or "").splitlines(), start=1):
                out.append((page_no, line_no, text))
            # pdfplumber caches every character object of every page it has read;
            # release each page, or a 70-page bulletin needs > 1 GB (the
            # serverless UDF memory limit).
            page.close()
    return out


def parse_pdf(pdf) -> ParseResult:
    return parse_lines(extract_lines(pdf))


def read_header(pdf) -> ParseResult:
    """Trade date, bulletin number and FINAL/PRELIMINARY from page 1 only (fast)."""
    return parse_lines(extract_lines(pdf, max_pages=1))


if __name__ == "__main__":
    import sys
    from collections import Counter

    res = parse_pdf(sys.argv[1])
    print(f"trade_date={res.trade_date} bulletin=#{res.bulletin_number} status={res.status}")
    print(
        f"rows={len(res.rows)} rejects={len(res.rejects)} "
        f"blocks={len(res.totals)} mismatched_totals={len(res.mismatched_totals)}"
    )
    blocks = Counter((r.product_code, r.contract_month) for r in res.rows)
    for (c, mth), n in sorted(blocks.items()):
        print(f"  {c:9s} {mth}  {n:4d} rows")
