"""ENA 저장소에서 실행하는 테스트: ena_idea_lab.py 를 ENA 루트(또는 PYTHONPATH)에 두고 tests/ 로 복사해 쓴다. 이 저장소의 테스트 러너에서는 실행되지 않는다."""
import numpy as np

import ena_idea_lab as lab
from ena_bot.config import Config
from ena_bot.featx import CoinData, BAR_MS
from ena_bot.trend import target_positions


def test_asof_and_sma_and_rolling_rank():
    ts, v = np.array([10, 20, 30]), np.array([1.0, 2.0, 3.0])
    out = lab.asof(ts, v, np.array([5, 10, 25, 99]))
    assert np.isnan(out[0]) and list(out[1:]) == [1.0, 2.0, 3.0]
    assert np.allclose(lab.sma(np.arange(1.0, 6.0), 3)[2:], [2.0, 3.0, 4.0]) and np.isnan(lab.sma(np.arange(5.0), 3)[1])
    r = lab.rolling_rank(np.array([1.0, 2, 3, 4, 100.0]), 10, 3)
    assert np.isnan(r[2]) and r[4] == 1.0 and r[3] == 1.0          # 직전 값들보다 큰 값은 1.0, 현재 값은 포함하지 않음
    w = lab.rolling_rank(np.array([5.0, 4, 3, 2, 1, 0.5]), 3, 3)
    assert w[5] == 0.0                                             # 창(window)이 3이라 오래된 값은 빠진다


def test_judge_classification():
    base = {"full": {"sharpe": 0.67, "mdd": 25.0}, "hold": {"sharpe": 0.72, "mdd": 25.0}}
    good = {"full": {"sharpe": 0.81, "mdd": 21.0}, "hold": {"sharpe": 0.91, "mdd": 14.0}}
    assert lab.judge(base, good, {"full": [0.01, 0.3], "hold": [0.02, 0.5]})["verdict"].startswith("엄격 통과")
    assert lab.judge(base, good, {"full": [-0.1, 0.3], "hold": [0.02, 0.5]})["verdict"].startswith("점추정만")
    bad = {"full": {"sharpe": 0.70, "mdd": 25.0}, "hold": {"sharpe": 0.80, "mdd": 30.0}}
    assert lab.judge(base, bad, {"full": [0, 1], "hold": [0, 1]})["verdict"] == "폐기"


def test_render_stops_when_baseline_differs():
    out = {"baseline_check": {"got": {"full": (0.5, 1, 1)}, "expected": lab.BASE_EXPECT, "ok": False}}
    assert "중단" in lab.render(out)


def _coin(n, seed):
    rng = np.random.default_rng(seed)
    ts = (1_600_000_000_000 // BAR_MS + np.arange(n)) * BAR_MS
    c = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.01, n)))
    bars = np.c_[ts, c, c * 1.002, c * 0.998, c, np.ones(n)]
    fund = np.c_[ts[::2], rng.normal(0.0001, 0.0004, len(ts[::2]))]
    oi = np.c_[ts, 1e6 * np.exp(np.cumsum(rng.normal(0, 0.05, n)))]
    ls = np.c_[ts, 0.5 + 0.1 * rng.standard_normal(n)]
    prem = np.c_[ts, rng.normal(0, 0.001, n)]
    return CoinData(bars=bars, funding=fund, oi=oi, ls=ls, prem=prem)


def test_overlays_never_use_future_information():
    """뒤쪽 데이터를 잘라 내도 앞쪽 구간의 배수가 같아야 한다(미래 누수 방지)."""
    cfg = Config()
    n, m = 2600, 2000
    full = _coin(n, 1)
    cut_ts = full.bars[m, 0]
    part = CoinData(bars=full.bars[:m], funding=full.funding[full.funding[:, 0] < cut_ts], oi=full.oi[full.oi[:, 0] < cut_ts],
                    ls=full.ls[full.ls[:, 0] < cut_ts], prem=full.prem[full.prem[:, 0] < cut_ts])
    def ctx_for(cd):
        b = cd.bars
        below = (b[:, 4] < lab.sma(b[:, 4], 1200)).astype(float)
        below[~np.isfinite(lab.sma(b[:, 4], 1200))] = 0.0
        return {"btc_ts": b[:, 0], "btc_below": below, "macro": {}, "macro_close_ms": np.zeros(0, dtype=np.int64)}
    a = lab.overlays_for("X", full, target_positions(full.bars[:, 4], cfg), cfg, ctx_for(full))
    b = lab.overlays_for("X", part, target_positions(part.bars[:, 4], cfg), cfg, ctx_for(part))
    for k in lab.CRYPTO:
        for side in (0, 1):
            assert np.array_equal(a[k][side][:m - 2], b[k][side][:m - 2]), f"{k} 가 미래 데이터에 의존합니다"
