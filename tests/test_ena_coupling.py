import pathlib
import sys

import numpy as np

D = pathlib.Path(__file__).resolve().parents[1] / "patches/ena-bot/files"
sys.path.insert(0, str(D))
import ena_coupling as c  # noqa: E402
import ena_macro_study as m  # noqa: E402
sys.path.pop(0)


def make(coupled, n=140, seed=0):
    """영업일 n 일의 가짜 나스닥(수익률 랜덤)과 시간봉 코인: coupled 이면 코인 일간 수익률 = 1.5×나스닥 + 잡음."""
    import datetime as dt
    rng = np.random.default_rng(seed)
    days, d = [], dt.date(2025, 1, 6)
    while len(days) <= n:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    nq_ret = rng.normal(0, 0.01, n)
    idx, v = [(days[0].isoformat(), 100.0)], 100.0
    for r, dd in zip(nq_ret, days[1:]):
        v *= 1 + r
        idx.append((dd.isoformat(), v))
    coin_ret = (1.5 * nq_ret if coupled else 0.0) + rng.normal(0, 0.01, n)
    hourly, p = {}, 1.0
    t0 = m.us_close_ms(days[0].isoformat())
    hourly[t0 // m.HOUR * m.HOUR - m.HOUR] = p
    for r, dd in zip(coin_ret, days[1:]):
        p *= 1 + r
        hourly[m.us_close_ms(dd.isoformat()) // m.HOUR * m.HOUR - m.HOUR] = p
    return idx, hourly


def test_coupled_series_shows_high_correlation_and_independent_one_does_not():
    idx, h = make(True)
    r = c.study(idx, h, h)
    assert r["n"] == 140 and r["ena_nq"] > 0.6 and 1.2 < r["beta_ena"] < 1.8 and r["ena_nq_ci"][0] > 0.4
    idx2, h2 = make(False)
    r2 = c.study(idx2, h2, h2)
    assert abs(r2["ena_nq"]) < 0.3 and r2["ena_nq_ci"][0] < 0.1


def test_short_history_is_not_computed_and_render_runs():
    idx, h = make(True, n=40)
    assert c.study(idx, h, h)["n"] == 40 and "계산하지 않았습니다" in c.render(c.study(idx, h, h), "나스닥")
    idx, h = make(True)
    text = c.render(c.study(idx, h, h), "나스닥 종합")
    assert "상관" in text and "60일 이동 상관" in text and "최근 30거래일" in text


def test_partial_correlation_removes_the_common_btc_factor():
    rng = np.random.default_rng(3)
    btc = rng.normal(0, 1, 400)
    nq, ena = 0.8 * btc + rng.normal(0, 0.3, 400), 0.8 * btc + rng.normal(0, 0.3, 400)
    assert c.corr(nq, ena) > 0.8 and abs(c.partial(nq, ena, btc)) < 0.2
