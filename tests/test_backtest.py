import math
import random

import pytest

from options_math import levels
from options_math.backtest import evaluate


def test_settle_inside_bands_and_walls():
    out = evaluate(
        forward=4000, atm_vol=0.2, years=1 / 252, settle=4010,
        sd1_low=3950, sd1_high=4050, sd2_low=3900, sd2_high=4100,
        put_wall=3975, call_wall=4025, max_pain=4000, gamma_flip=3990,
    )  # fmt: skip
    assert (out["in_1sd"], out["in_2sd"], out["inside_walls"]) == (True, True, True)
    assert out["move"] == 10
    assert out["positive_gamma"] is True


def test_max_pain_closer_than_forward():
    out = evaluate(forward=4000, atm_vol=0.2, years=0.01, settle=4040, max_pain=4050)
    assert out["max_pain_error"] == 10 and out["forward_error"] == 40
    assert out["max_pain_closer"] is True


def test_missing_levels_give_none_not_false():
    out = evaluate(forward=4000, atm_vol=None, years=0.01, settle=4100)
    assert out["in_1sd"] is None and out["move_sd"] is None and out["max_pain_closer"] is None


def test_move_in_sd_units():
    v = 0.2 * math.sqrt(4 / 252)
    settle = 4000 * math.exp(1.5 * v)
    assert evaluate(forward=4000, atm_vol=0.2, years=4 / 252, settle=settle)["move_sd"] == (
        pytest.approx(1.5)
    )


def test_calibrated_bands_hold_about_68_and_95_percent():
    """Simulated lognormal settlements at the band's own vol: the hit rates the
    backtest reports should then match the textbook 68% / 95%."""
    rng = random.Random(1)
    forward, vol, years, n = 4000, 0.22, 3 / 252, 20000
    bands = levels.sd_bands(forward, vol, years)
    hits1 = hits2 = 0
    for _ in range(n):
        settle = forward * math.exp(rng.gauss(0, vol * math.sqrt(years)))
        out = evaluate(forward=forward, atm_vol=vol, years=years, settle=settle, **{
            k: bands[k] for k in ("sd1_low", "sd1_high", "sd2_low", "sd2_high")})  # fmt: skip
        hits1 += out["in_1sd"]
        hits2 += out["in_2sd"]
    assert hits1 / n == pytest.approx(0.683, abs=0.01)
    assert hits2 / n == pytest.approx(0.954, abs=0.01)
