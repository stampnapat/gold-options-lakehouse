"""Infer put/call side and expiry for bulletin rows that do not print them.

The Monday-Thursday weekly blocks (GMW/GWT/GWW/GWR) mix several expiries and both
sides under one header. Each row still carries its strike, settlement and delta,
which pin it down with Black-76:

1. Forward curve. Monthly OG rows do print their side, so each near-the-money row
   yields the futures price (`black76.implied_forward`). The median per futures
   month is the curve, e.g. DEC26 4114.5, FEB27 4149.7.
2. Candidates. The calendar gives the possible expiries of the block; each expiry
   has a known underlying futures month, hence a forward.
3. Score every (side, expiry) hypothesis for a row. With the forward fixed, the
   premium gives an implied v; the model delta under that v must match the printed
   delta (this separates calls from puts and Dec from Feb underlyings), and
   sigma = v / sqrt(T) must sit near a reference smile taken from the monthly
   options (this separates one week from the next).
4. Within a strike, each (side, expiry) can be used once; hypotheses are taken
   best-score first.

Friday weeklies and monthlies already have their side; only their expiry is
filled in from the calendar. Rows that fit no hypothesis keep side/expiry None.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from options_math import black76

from . import calendar as cal

DELTA_TOL = 0.01  # one score unit = 0.01 of delta error ...
SIGMA_TOL = 0.15  # ... or 15% away from the reference vol
MAX_SIGMA_LOG_ERROR = math.log(3)  # reject fits with vol < 1/3 or > 3x the smile
INTRINSIC_TOL = 0.15  # settlement vs intrinsic (forward is read to ~0.1)
EXPIRED_DELTA = 0.9999  # printed delta of an option settled on its last day


@dataclass(frozen=True)
class Inference:
    side: str | None
    expiry: date | None
    underlying: str | None
    forward: float | None
    implied_vol: float | None  # annualized, on business days / 252
    delta_error: float | None
    score: float | None
    score_margin: float | None  # gap to the best hypothesis that differs; low = unsure
    method: str  # printed | model | intrinsic | moneyness (side only) | unresolved


def years_to(trade_date: date, expiry: date) -> float:
    return cal.business_days_between(trade_date, expiry) / 252


def forward_curve(rows) -> dict[str, float]:
    """Futures price per futures month, read off monthly OG rows (side printed)."""
    samples = defaultdict(list)
    for r in rows:
        if r.product_code in ("OG CALL", "OG PUT") and r.delta and 0.2 < r.delta < 0.8:
            solved = black76.implied_forward(r.side, r.strike, r.settle, r.delta)
            if solved:
                samples[cal.futures_month(r.contract_month)].append(solved[0])
    return {m: statistics.median(v) for m, v in samples.items() if len(v) >= 5}


def reference_smile(rows, trade_date: date, curve: dict[str, float]) -> list[tuple[float, float]]:
    """(strike, sigma) from the nearest monthly OG, out-of-the-money side only."""
    months = sorted(
        {r.contract_month for r in rows if r.product_code in ("OG CALL", "OG PUT")},
        key=cal.monthly_expiry,
    )
    for month in months:
        expiry = cal.monthly_expiry(month)
        forward = curve.get(cal.futures_month(month))
        if expiry <= trade_date or forward is None:
            continue
        t = years_to(trade_date, expiry)
        smile = []
        for r in rows:
            otm = (r.side == "C") == (r.strike >= forward)
            if r.contract_month == month and r.side and otm and r.settle:
                v = black76.implied_v(r.side, forward, r.strike, r.settle)
                if v:
                    smile.append((r.strike, v / math.sqrt(t)))
        if len(smile) >= 5:
            return sorted(smile)
    return []


def _interp(smile: list[tuple[float, float]], strike: float) -> float:
    if strike <= smile[0][0]:
        return smile[0][1]
    if strike >= smile[-1][0]:
        return smile[-1][1]
    for (k0, s0), (k1, s1) in zip(smile, smile[1:], strict=False):
        if k0 <= strike <= k1:
            return s0 + (s1 - s0) * (strike - k0) / (k1 - k0) if k1 > k0 else s0
    return smile[-1][1]


def _hypotheses(row, trade_date, candidates, curve, smile, scale):
    """Scored (score, side, expiry, underlying, forward, sigma, delta_error) tuples."""
    out = []
    for expiry in candidates:
        underlying = cal.underlying_for(expiry)
        forward = curve.get(underlying)
        if forward is None:
            continue
        t = years_to(trade_date, expiry)
        for side in "CP":
            v = black76.implied_v(side, forward, row.strike, row.settle)
            if v is None:
                continue
            sigma = v / math.sqrt(t)
            d_err = abs(black76.delta(side, forward, row.strike, v) - row.delta)
            s_err = abs(math.log(sigma / (_interp(smile, row.strike) * scale)))
            if s_err > MAX_SIGMA_LOG_ERROR:
                continue  # a vol this far off the smile is not a real fit
            score = d_err / DELTA_TOL + s_err / SIGMA_TOL
            out.append((score, side, expiry, underlying, forward, sigma, d_err))
    return sorted(out, key=lambda h: h[0])


def _intrinsic_side(row, curve, expiry: date) -> str | None:
    """An option settling at intrinsic on its last day: whichever side matches."""
    forward = curve.get(cal.underlying_for(expiry))
    if forward is None or row.settle is None:
        return None
    gaps = {s: abs(black76.price(s, forward, row.strike, 0.0) - row.settle) for s in "CP"}
    side = min(gaps, key=gaps.get)
    return side if gaps[side] <= INTRINSIC_TOL else None


def infer(rows, trade_date: date) -> list[Inference]:
    """One Inference per row, same order. `rows` are one bulletin's OptionRows."""
    curve = forward_curve(rows)
    smile = reference_smile(rows, trade_date, curve)
    result: list[Inference | None] = [None] * len(rows)
    weekly = defaultdict(list)  # (product, month) -> row indexes

    for i, r in enumerate(rows):
        if r.side is not None:  # monthly or Friday weekly: side printed
            if r.product_code in ("OG CALL", "OG PUT"):
                expiry = cal.monthly_expiry(r.contract_month)
            else:
                dates = cal.weekly_expiries(r.product_code, r.contract_month)
                expiry = dates[0] if dates else None
            underlying = cal.underlying_for(expiry) if expiry else None
            forward = curve.get(underlying)
            sigma = None
            if forward and r.settle and expiry and expiry > trade_date:
                v = black76.implied_v(r.side, forward, r.strike, r.settle)
                sigma = v / math.sqrt(years_to(trade_date, expiry)) if v else None
            result[i] = Inference(
                r.side, expiry, underlying, forward, sigma, None, None, None, "printed"
            )
        else:
            weekly[(r.product_code, r.contract_month)].append(i)

    for (product, month), idxs in weekly.items():
        dates = cal.weekly_expiries(product, month)
        live = [d for d in dates if d > trade_date]
        expiring = trade_date if trade_date in dates else None

        # Block-level vol scale: weekly vols run below/above the monthly smile.
        scale = 1.0
        if smile:
            for _ in range(3):
                ratios = []
                for i in idxs:
                    r = rows[i]
                    if r.settle and r.delta and 0.15 < r.delta < 0.85:
                        hyps = _hypotheses(r, trade_date, live, curve, smile, scale)
                        if hyps:
                            ratios.append(hyps[0][5] / _interp(smile, r.strike))
                if ratios:
                    scale = statistics.median(ratios)

        by_strike = defaultdict(list)
        for i in idxs:
            by_strike[rows[i].strike].append(i)

        for strike_idxs in by_strike.values():
            # Options expiring on the trade date settle at intrinsic with delta 1;
            # take them out first so they cannot claim a live expiry.
            expired = {
                i
                for i in strike_idxs
                if expiring
                and rows[i].delta is not None
                and rows[i].delta >= EXPIRED_DELTA
                and _intrinsic_side(rows[i], curve, expiring)
            }
            hyps = {
                i: _hypotheses(rows[i], trade_date, live, curve, smile, scale)
                if smile and rows[i].settle and rows[i].delta and i not in expired
                else []
                for i in strike_idxs
            }
            ranked = sorted((h[0], i, h) for i in strike_idxs for h in hyps[i])
            used, chosen = set(), {}
            for _, i, h in ranked:
                if i not in chosen and (h[1], h[2]) not in used:
                    used.add((h[1], h[2]))
                    chosen[i] = h
            for i in strike_idxs:
                result[i] = _resolve(rows[i], chosen.get(i), hyps[i], curve, live, expiring)
    return result  # type: ignore[return-value]


def _resolve(row, chosen, hyps, curve, live, expiring) -> Inference:
    if chosen:
        score, side, expiry, underlying, forward, sigma, d_err = chosen
        others = [h[0] for h in hyps if (h[1], h[2]) != (side, expiry)]
        margin = min(others) - score if others else None
        return Inference(side, expiry, underlying, forward, sigma, d_err, score, margin, "model")
    side = _intrinsic_side(row, curve, expiring) if expiring else None
    if side:
        underlying = cal.underlying_for(expiring)
        return Inference(
            side, expiring, underlying, curve.get(underlying), None, None, None, None, "intrinsic"
        )
    # Minimum-tick premium and no delta: far out of the money, so the side follows
    # from the strike, but nothing tells the expiries apart.
    if live and row.settle is not None and row.delta is None:
        forward = curve.get(cal.underlying_for(live[0]))
        if forward and abs(row.strike / forward - 1) > 0.03:
            side = "P" if row.strike < forward else "C"
            return Inference(side, None, None, forward, None, None, None, None, "moneyness")
    return Inference(None, None, None, None, None, None, None, None, "unresolved")
