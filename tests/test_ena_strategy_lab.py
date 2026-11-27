import pathlib
import sys
import types

import numpy as np

D = pathlib.Path(__file__).resolve().parents[1] / "patches/ena-bot/files"
sys.path.insert(0, str(D))
import ena_strategy_lab as lab  # noqa: E402
sys.path.pop(0)

CFG = types.SimpleNamespace(trend=types.SimpleNamespace(lookbacks=[2, 3]))


def test_take_profit_exits_at_4pct_and_waits_for_a_new_signal():
    c = np.array([100, 101, 102, 103, 108, 109, 110, 111, 112], float)
    pos = lab.d_trend_tp(c, c, c, CFG)
    assert pos[3] == 1 and pos[4] == 0 and pos[5] == 0 and pos[8] == 0      # 4% 넘으면 청산, 같은 방향 신호가 이어져도 재진입 없음


def test_donchian_and_mean_reversion_do_not_use_the_future():
    rng = np.random.default_rng(1)
    c = np.cumprod(1 + rng.normal(0, 0.01, 600)) * 100
    for fn in (lab.d_donchian, lab.d_rsi, lab.d_boll, lab.d_dip, lab.d_ema_cross):
        full = fn(c, c * 1.002, c * 0.998, CFG)
        cut = fn(c[:450], c[:450] * 1.002, c[:450] * 0.998, CFG)
        assert np.array_equal(full[:450], cut), fn.__name__              # 뒤 데이터를 지워도 앞 신호가 같다


def test_indicators_basic():
    assert abs(lab.sma(np.arange(10.0), 5)[-1] - 7.0) < 1e-9
    assert lab.rsi(np.arange(1.0, 40.0))[-1] == 100.0
    ci = lab.boot_ci(np.ones(50) * 2.0)
    assert ci == (2.0, 2.0) and lab.boot_ci([1, 2]) is None
