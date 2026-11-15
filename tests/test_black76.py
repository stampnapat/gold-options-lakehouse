import math

import pytest

from options_math import black76


@pytest.mark.parametrize("side", ["C", "P"])
def test_implied_v_round_trips(side):
    premium = black76.price(side, 4000, 4050, 0.03)
    assert black76.implied_v(side, 4000, 4050, premium) == pytest.approx(0.03, abs=1e-9)


def test_put_call_parity():
    c = black76.price("C", 4000, 3950, 0.04)
    p = black76.price("P", 4000, 3950, 0.04)
    assert c - p == pytest.approx(4000 - 3950)


def test_implied_v_rejects_premium_below_intrinsic():
    assert black76.implied_v("C", 4100, 4000, 99.0) is None


@pytest.mark.parametrize(("side", "strike"), [("C", 4100), ("P", 3950), ("C", 3900)])
def test_implied_forward_recovers_futures_price(side, strike):
    premium = black76.price(side, 4040, strike, 0.035)
    d = black76.delta(side, 4040, strike, 0.035)
    forward, v = black76.implied_forward(side, strike, premium, d)
    assert forward == pytest.approx(4040, abs=0.01)
    assert v == pytest.approx(0.035, abs=1e-6)


def test_sd_move_is_lognormal():
    down, up = black76.sd_move(4000, 0.25, 1 / 252, k=2)
    v = 0.25 * math.sqrt(1 / 252)
    assert (down, up) == pytest.approx(
        (4000 * (math.exp(-2 * v) - 1), 4000 * (math.exp(2 * v) - 1))
    )
    assert -down < up  # the up move is larger, as in the CME SD table
