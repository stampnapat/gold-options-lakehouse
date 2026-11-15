import pytest

from options_math import levels
from options_math.levels import Strike


def test_max_pain_minimizes_holder_payout():
    strikes = [Strike(90, put_oi=100), Strike(100, call_oi=10, put_oi=10), Strike(110, call_oi=100)]
    assert levels.max_pain(strikes) == 100


def test_max_pain_none_without_open_interest():
    assert levels.max_pain([Strike(100)]) is None


def test_walls_stay_inside_the_band_and_on_their_side():
    strikes = [
        Strike(3900, put_oi=999),  # outside the band
        Strike(3980, put_oi=50, call_oi=500),  # call OI below the forward is ignored
        Strike(3990, put_oi=80),
        Strike(4050, call_oi=70),
        Strike(4300, call_oi=999),  # outside the band
    ]
    w = levels.walls(strikes, low=3950, high=4100, forward=4000)
    assert (w["put_wall"], w["put_wall_oi"], w["call_wall"], w["call_wall_oi"]) == (
        3990, 80, 4050, 70,
    )  # fmt: skip


def test_atm_vol_interpolates_otm_side():
    strikes = [Strike(3950, put_iv=0.30, call_iv=0.99), Strike(4050, call_iv=0.20, put_iv=0.99)]
    assert levels.atm_vol(strikes, 4000) == pytest.approx(0.25)


def test_sd_bands_widen_with_k():
    b = levels.sd_bands(4000, 0.25, 5 / 252)
    assert b["sd3_low"] < b["sd2_low"] < b["sd1_low"] < 4000 < b["sd1_high"] < b["sd2_high"]


def test_gamma_flip_sits_between_put_and_call_open_interest():
    # Mirror image: puts below, calls above -> GEX crosses zero at the forward.
    strikes = [Strike(3950, put_oi=100, put_iv=0.2), Strike(4050, call_oi=100, call_iv=0.2)]
    flip = levels.gamma_flip(strikes, forward=4000, years=5 / 252, vol=0.2)
    assert flip == pytest.approx(4000, abs=5)
    assert (
        levels.gex_at(strikes, 3950, 5 / 252, 0.2) < 0 < levels.gex_at(strikes, 4050, 5 / 252, 0.2)
    )


def test_expiry_levels_without_vol_skips_vol_fields():
    out = levels.expiry_levels([Strike(4000, call_oi=1, put_oi=3)], forward=4000, years=0.01)
    assert out["put_call_oi_ratio"] == 3
    assert "sd1_low" not in out
