"""Black-76: European options on futures (CME gold options are priced off GC futures).

Undiscounted, which is accurate to well under one tick for the few weeks of
life a weekly option has. `vol_sqrt_t` (v = sigma * sqrt(T)) is used where the
time to expiry is not yet known.
"""

from __future__ import annotations

import math
from statistics import NormalDist

_N = NormalDist()


def _d1(forward: float, strike: float, v: float) -> float:
    return math.log(forward / strike) / v + v / 2


def price(side: str, forward: float, strike: float, v: float) -> float:
    if v <= 0:
        return max(forward - strike, 0.0) if side == "C" else max(strike - forward, 0.0)
    d1 = _d1(forward, strike, v)
    d2 = d1 - v
    if side == "C":
        return forward * _N.cdf(d1) - strike * _N.cdf(d2)
    return strike * _N.cdf(-d2) - forward * _N.cdf(-d1)


def delta(side: str, forward: float, strike: float, v: float) -> float:
    """Absolute delta, as the bulletin prints it (no sign for puts)."""
    n = _N.cdf(_d1(forward, strike, v))
    return n if side == "C" else 1 - n


def implied_v(side: str, forward: float, strike: float, premium: float) -> float | None:
    """v = sigma*sqrt(T) that reproduces `premium`, or None if no v can (premium at or
    below intrinsic, or above the forward/strike bound)."""
    intrinsic = price(side, forward, strike, 0.0)
    if premium <= intrinsic or premium >= (forward if side == "C" else strike):
        return None
    lo, hi = 1e-6, 3.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if price(side, forward, strike, mid) < premium:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def sd_move(forward: float, sigma: float, years: float, k: float = 1.0) -> tuple[float, float]:
    """Price moves for a k-standard-deviation band, lognormal: (down, up), down < 0."""
    v = sigma * math.sqrt(years)
    return forward * (math.exp(-k * v) - 1), forward * (math.exp(k * v) - 1)


def implied_forward(side: str, strike: float, premium: float, abs_delta: float):
    """Solve forward AND v from one option's premium and printed delta.

    With side fixed, delta gives d1, and d1 ties forward to v:
    F = K * exp(v*d1 - v^2/2). Substituting into the price leaves one equation in v.
    Returns (forward, v) or None. Used to read the futures price off the monthly
    options, whose side is known, without needing the futures bulletin.
    """
    if not premium or not 0 < abs_delta < 1:
        return None
    d1 = _N.inv_cdf(abs_delta) if side == "C" else -_N.inv_cdf(abs_delta)

    def fwd(v: float) -> float:
        return strike * math.exp(v * d1 - v * v / 2)

    def gap(v: float) -> float:
        return price(side, fwd(v), strike, v) - premium

    prev, g_prev = 1e-5, gap(1e-5)
    for i in range(1, 400):
        v = i * 0.0025
        g = gap(v)
        if (g > 0) != (g_prev > 0):
            lo, hi = prev, v
            for _ in range(60):
                mid = (lo + hi) / 2
                if (gap(mid) > 0) == (gap(lo) > 0):
                    lo = mid
                else:
                    hi = mid
            v = (lo + hi) / 2
            return fwd(v), v
        prev, g_prev = v, g
    return None
