"""Score one set of levels against where the futures actually settled at expiry.

Point in time: the levels come from a bulletin published before the expiry, the
outcome from the bulletin of the expiry date itself. Only settlement prices are
available (no intraday highs and lows), so these are close-to-close questions:

- Did the expiry settlement land inside the 1 SD / 2 SD band? For a well-priced
  lognormal band that should happen about 68% / 95% of the time.
- Did it land between the put wall and the call wall?
- Was max pain closer to the settlement than the forward (the naive forecast)?
- How big was the move in SD units, split by gamma regime (forward above the
  gamma flip = positive dealer gamma, expected to dampen moves)?
"""

from __future__ import annotations

import math


def evaluate(
    *,
    forward: float,
    atm_vol: float | None,
    years: float,
    settle: float,
    sd1_low: float | None = None,
    sd1_high: float | None = None,
    sd2_low: float | None = None,
    sd2_high: float | None = None,
    put_wall: float | None = None,
    call_wall: float | None = None,
    max_pain: float | None = None,
    gamma_flip: float | None = None,
) -> dict:
    def between(lo, hi):
        return None if lo is None or hi is None else lo <= settle <= hi

    move_sd = None
    if atm_vol and years > 0:
        move_sd = math.log(settle / forward) / (atm_vol * math.sqrt(years))
    forward_error = abs(settle - forward)
    max_pain_error = abs(settle - max_pain) if max_pain is not None else None
    return {
        "settle": settle,
        "move": settle - forward,
        "move_sd": move_sd,
        "in_1sd": between(sd1_low, sd1_high),
        "in_2sd": between(sd2_low, sd2_high),
        "inside_walls": between(put_wall, call_wall),
        "forward_error": forward_error,
        "max_pain_error": max_pain_error,
        "max_pain_closer": None if max_pain_error is None else max_pain_error < forward_error,
        "positive_gamma": None if gamma_flip is None else forward > gamma_flip,
    }
