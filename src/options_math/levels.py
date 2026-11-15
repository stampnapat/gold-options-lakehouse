"""Levels an options-flow trader reads off one expiry's strikes.

Input is one expiry on one trade date: per strike, call/put open interest and
implied vol, plus the forward and years to expiry. Everything is pure Python so
the same code runs in tests and inside the Spark pipeline.

Conventions
- SD bands are lognormal around the forward: F * (exp(+-k * atm_vol * sqrt(T)) - 1).
- GEX (gamma exposure) assumes dealers are long the calls and short the puts that
  customers hold the other side of, the usual public convention: call gamma counts
  positive, put gamma negative. Units: dollars of futures delta per 1% move,
  gamma * OI * 100 oz * F^2 * 0.01. The gamma flip is the futures price at which
  total GEX changes sign, holding each strike's vol fixed (sticky strike).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import black76

CONTRACT_OZ = 100  # COMEX gold: 100 troy ounces per contract


@dataclass(frozen=True)
class Strike:
    strike: float
    call_oi: int = 0
    put_oi: int = 0
    call_iv: float | None = None
    put_iv: float | None = None


def atm_vol(strikes: list[Strike], forward: float) -> float | None:
    """Vol at the forward, interpolated from the out-of-the-money side of each strike."""
    smile = []
    for s in sorted(strikes, key=lambda s: s.strike):
        iv = (s.call_iv or s.put_iv) if s.strike >= forward else (s.put_iv or s.call_iv)
        if iv:
            smile.append((s.strike, iv))
    below = [p for p in smile if p[0] <= forward]
    above = [p for p in smile if p[0] >= forward]
    if not below or not above:
        return None
    (k0, v0), (k1, v1) = below[-1], above[0]
    return v0 if k1 == k0 else v0 + (v1 - v0) * (forward - k0) / (k1 - k0)


def sd_bands(forward: float, vol: float, years: float) -> dict[str, float]:
    out = {}
    for k in (1, 2, 3):
        down, up = black76.sd_move(forward, vol, years, k)
        out[f"sd{k}_low"], out[f"sd{k}_high"] = forward + down, forward + up
    return out


def max_pain(strikes: list[Strike]) -> float | None:
    """Settlement strike that minimizes what option holders are paid at expiry."""
    if not any(s.call_oi or s.put_oi for s in strikes):
        return None

    def payout(settle: float) -> float:
        return sum(
            s.call_oi * max(settle - s.strike, 0) + s.put_oi * max(s.strike - settle, 0)
            for s in strikes
        )

    return min((s.strike for s in strikes), key=payout)


def walls(strikes: list[Strike], low: float, high: float, forward: float) -> dict:
    """Largest call OI between forward and `high`, largest put OI between `low` and forward."""
    calls = [s for s in strikes if forward <= s.strike <= high and s.call_oi]
    puts = [s for s in strikes if low <= s.strike <= forward and s.put_oi]
    call = max(calls, key=lambda s: s.call_oi, default=None)
    put = max(puts, key=lambda s: s.put_oi, default=None)
    return {
        "call_wall": call.strike if call else None,
        "call_wall_oi": call.call_oi if call else None,
        "put_wall": put.strike if put else None,
        "put_wall_oi": put.put_oi if put else None,
    }


def gex_at(strikes: list[Strike], price: float, years: float, fallback_vol: float) -> float:
    """Total dealer GEX if futures traded at `price`."""
    total = 0.0
    for s in strikes:
        for oi, iv, sign in ((s.call_oi, s.call_iv, 1), (s.put_oi, s.put_iv, -1)):
            if oi:
                v = (iv or fallback_vol) * math.sqrt(years)
                g = black76.gamma(price, s.strike, v)
                total += sign * g * oi * CONTRACT_OZ * price * price * 0.01
    return total


def gamma_flip(strikes, forward, years, vol, span_sd: float = 3, steps: int = 240):
    """Futures price nearest the forward where GEX crosses zero, within +-span_sd."""
    v = vol * math.sqrt(years)
    lo, hi = forward * math.exp(-span_sd * v), forward * math.exp(span_sd * v)
    grid = [lo + (hi - lo) * i / steps for i in range(steps + 1)]
    values = [gex_at(strikes, p, years, vol) for p in grid]
    crossings = []
    for i in range(steps):
        (p0, g0), (p1, g1) = (grid[i], values[i]), (grid[i + 1], values[i + 1])
        if g0 == 0 or (g0 > 0) != (g1 > 0):
            crossings.append(p0 + (p1 - p0) * g0 / (g0 - g1) if g0 != g1 else p0)
    return min(crossings, key=lambda p: abs(p - forward), default=None)


def expiry_levels(strikes: list[Strike], forward: float, years: float) -> dict:
    """All levels for one expiry; vol-based fields are None when vol is unknown."""
    call_oi = sum(s.call_oi for s in strikes)
    put_oi = sum(s.put_oi for s in strikes)
    vol = atm_vol(strikes, forward)
    out = {
        "atm_vol": vol,
        "call_oi": call_oi,
        "put_oi": put_oi,
        "put_call_oi_ratio": put_oi / call_oi if call_oi else None,
        "max_pain": max_pain(strikes),
    }
    if vol and years > 0:
        bands = sd_bands(forward, vol, years)
        out |= bands
        out |= walls(strikes, bands["sd2_low"], bands["sd2_high"], forward)
        out["gex"] = gex_at(strikes, forward, years, vol)
        out["gamma_flip"] = gamma_flip(strikes, forward, years, vol)
    return out
