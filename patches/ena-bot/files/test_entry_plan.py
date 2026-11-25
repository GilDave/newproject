import math
import random

import pytest

from ena_bot import entry_plan as ep

DAY = 24 * 3600_000
T0 = 1_700_000_000_000                      # 실제 데이터처럼 에포크 밀리초


def ohlc(closes, seed=1, spread=0.004):
    rnd = random.Random(seed)
    o = [closes[0]] + closes[:-1]
    h = [max(a, b) * (1 + abs(rnd.gauss(0, spread))) for a, b in zip(o, closes)]
    l = [min(a, b) * (1 - abs(rnd.gauss(0, spread))) for a, b in zip(o, closes)]
    return o, h, l, closes


def path(segments, start=1.0, noise=0.004, seed=1):
    """(봉 수, 봉당 수익률) 구간들로 만든 종가 경로."""
    rnd = random.Random(seed)
    c = [start]
    for n, drift in segments:
        for _ in range(n):
            c.append(c[-1] * math.exp(drift + rnd.gauss(0, noise)))
    return c


def test_indicators_match_hand_calculations():
    assert ep.rsi([1, 2, 1, 2], 2)[2:] == [50.0, 75.0]                 # n=2 손계산: 이득·손실 평균 0.5/0.5 → 50, 이어서 0.75/0.25 → 75
    assert ep.rsi([5.0] * 30)[20] == 50.0 and ep.rsi(list(range(1, 40)))[30] == 100.0
    mid, up, lo = ep.bollinger([float(i) for i in range(1, 21)])
    assert mid[19] == 10.5 and up[19] == pytest.approx(10.5 + 2 * math.sqrt((400 - 1) / 12)) and math.isnan(mid[18]) and lo[19] == pytest.approx(21 - up[19])
    line, sig, hist = ep.macd([3.0] * 60)
    assert max(abs(x) for x in line + sig + hist) < 1e-12
    assert ep.ema([1.0, 2.0, 3.0], 3) == [1.0, 1.5, 2.25]


def test_trend_pullback_gives_a_long_plan_with_consistent_levels():
    o, h, l, c = ohlc(path([(180, 0.004), (7, -0.006)], seed=6), seed=6)          # 강한 상승 뒤 짧은 눌림
    ind = ep.indicators(o, h, l, c)
    t = len(c) - 1
    pb = [x for x in ep.candidates(ind, t) if x["setup"] == "pullback"]
    assert len(pb) == 1
    p = pb[0]
    a = ind["atr"][t]
    assert p["direction"] == "long" and p["aligned_trend"] and p["stop"] < p["zone"][0] < p["entry"] and p["entry"] < p["targets"][0] < p["targets"][1]
    risk = p["entry"] - p["stop"]
    t1, t2 = p["entry"] + min(1.5 * risk, ep.T1_MAX_ATR * a), p["entry"] + min(3.0 * risk, ep.T2_MAX_ATR * a)     # 목표는 위험의 1.5·3배, 단 한 번의 움직임(2.5·4.5 ATR) 안으로 제한
    assert p["targets"] == [pytest.approx(t1), pytest.approx(t2)] and p["rr"] == pytest.approx((t1 - p["entry"]) / risk) and p["risk_pct"] == pytest.approx(risk / p["entry"])
    assert ep.MIN_STOP_ATR * a <= risk <= ep.MAX_STOP_ATR * a and risk <= ep.MAX_STOP_PCT * p["entry"]
    assert p["state"] in ("in_zone", "approaching", "waiting", "broken", "poor_rr", "weak") and len(p["checks"]) == 3 and p["score"] == sum(1 for k in p["checks"] if k["ok"])
    assert (p["state"] == "weak") == (p["score"] < ep.MIN_SHOW_SCORE and p["state"] != "poor_rr" and p["state"] == "weak")      # 조건이 적게 겹친 계획만 '조건 부족'


def test_downtrend_pullback_is_the_mirror_image():
    o, h, l, c = ohlc(path([(180, -0.004), (7, 0.006)], seed=9), seed=9)
    ind = ep.indicators(o, h, l, c)
    t = len(c) - 1
    pb = [x for x in ep.candidates(ind, t) if x["setup"] == "pullback"]
    assert len(pb) == 1 and pb[0]["direction"] == "short" and pb[0]["aligned_trend"]
    p = pb[0]
    assert p["stop"] > p["zone"][1] > p["entry"] > p["targets"][0] > p["targets"][1]
    risk = p["stop"] - p["entry"]
    a = ind["atr"][t]
    assert p["targets"][0] == pytest.approx(p["entry"] - min(1.5 * risk, ep.T1_MAX_ATR * a)) and p["targets"][1] == pytest.approx(p["entry"] - min(3.0 * risk, ep.T2_MAX_ATR * a))


def test_band_reversion_long_after_a_sharp_drop_targets_the_mid_and_upper_band():
    c = path([(150, 0.0), (6, -0.02)], noise=0.003)                    # 횡보 뒤 급락: 아래 밴드 이탈
    o, h, l, c = ohlc(c)
    ind = ep.indicators(o, h, l, c)
    t = len(c) - 1
    rev = [x for x in ep.candidates(ind, t) if x["setup"] == "reversion" and x["direction"] == "long"]
    assert len(rev) == 1
    p = rev[0]
    a = ind["atr"][t]
    e = ind["bb_lo"][t]
    assert p["entry"] == pytest.approx(e)
    assert p["targets"] == [pytest.approx(e + min(ind["bb_mid"][t] - e, ep.T1_MAX_ATR * a)), pytest.approx(e + min(ind["bb_up"][t] - e, ep.T2_MAX_ATR * a))]     # 볼린저 중심선·반대편 밴드, 단 현실적인 거리 안으로 제한
    assert p["stop"] < p["entry"] and ind["rsi"][t] < 35 and [k["ok"] for k in p["checks"]][:2] == [True, True]
    assert p["rr"] == pytest.approx((p["targets"][0] - p["entry"]) / (p["entry"] - p["stop"]))


def test_a_reversion_with_too_little_reward_is_marked_poor_rr_and_far_prices_do_not_make_one():
    c = path([(150, 0.0), (6, -0.02)], noise=0.003)
    o, h, l, c = ohlc(c)
    ind = ep.indicators(o, h, l, c)
    wave = ohlc([1.0 + 0.01 * math.sin(i / 5) for i in range(260)])
    wind = ep.indicators(*wave)
    for k in range(ep.MIN_BARS, 260):                                   # 모든 시점에서: 밴드 근처(%B 극단)이거나 진입 구간에 가까울 때만 반전 계획이 있다
        snap = ep.snapshot(wind, k)
        for x in ep.candidates(wind, k):
            if x["setup"] == "reversion":
                assert snap["pct_b"] <= 0.1 or snap["pct_b"] >= 0.9 or x["state"] in ("in_zone", "approaching", "poor_rr", "weak")      # poor_rr·weak: 가까이 왔지만 이득이 작거나 조건이 적어 대기
    t = len(c) - 1
    for p in ep.candidates(ind, t):
        if p["state"] == "poor_rr":
            assert p["rr"] < ep.MIN_RR                                  # 현실적으로 제한한 첫 목표의 손익비가 모자라면 대기
        elif p["state"] in ("in_zone", "approaching", "waiting"):
            assert p["rr"] >= ep.MIN_RR


def test_nothing_uses_future_bars_and_short_series_give_nothing():
    c = path([(170, 0.003), (8, -0.005)])
    o, h, l, c = ohlc(c)
    t = len(c) - 1
    base = ep.candidates(ep.indicators(o, h, l, c), t)
    c2, o2, h2, l2 = c[:], o[:], h[:], l[:]
    for k in (c2, o2, h2, l2):
        k.extend([k[-1] * 5] * 30)                                      # 이후 가격을 크게 바꿔도 봉 t의 계획은 같다
    assert ep.candidates(ep.indicators(o2, h2, l2, c2), t) == base
    short = ohlc([1.0 + 0.001 * i for i in range(60)])
    assert ep.candidates(ep.indicators(*short), 59) == []


def test_plans_pick_the_most_favorable_timeframe_and_isolate_failures():
    def rows(closes, step):
        o, h, l, c = ohlc(closes)
        return [[T0 + i * step, o[i], h[i], l[i], c[i], 1000.0] for i in range(len(c))][::-1]     # 거래소처럼 최신이 앞(내림차순)
    now = T0 + 400 * DAY
    data = {"15": rows(path([(250, 0.0), (6, -0.02)], noise=0.003), 15 * 60_000), "60": rows(path([(180, 0.004), (7, -0.006)]), 3600_000),
            "240": rows(path([(200, 0.003)]), 4 * 3600_000), "D": None}

    def fetch(iv, n):
        if data[iv] is None:
            raise RuntimeError("rate limit")
        return data[iv]
    res = ep.plans(fetch, now)
    tf = res["timeframes"]
    assert tf["1d"]["status"] == "error" and "rate limit" in tf["1d"]["reason"] and tf["1h"]["status"] == "ok"       # 한 시간봉의 실패가 나머지를 막지 않는다
    assert any(x["setup"] == "reversion" and x["direction"] == "long" for x in tf["15m"]["all"]) and tf["1h"]["best"] is not None      # 급락한 15분봉에는 밴드 반전 후보가 있다
    fav = res["favorable"]
    assert fav is None or tf[fav]["best"]["state"] in ("in_zone", "approaching")
    if fav:
        assert tf[fav]["best"]["score"] >= 2 and "htf_aligned" in tf[fav]["best"]
    assert res["note"] is None if fav else "대기" in res["note"]


def test_forming_bar_is_excluded_from_signals_but_gives_the_current_price():
    o, h, l, c = ohlc(path([(180, 0.004), (7, -0.006)]))
    step = 3600_000
    rows = [[T0 + i * step, o[i], h[i], l[i], c[i], 1.0] for i in range(len(c))]
    closed = ep.plan_timeframe(rows, step, now_ms=T0 + len(c) * step + 1)                  # 마지막 봉도 마감됨
    forming = ep.plan_timeframe(rows, step, now_ms=T0 + (len(c) - 1) * step + step // 2)   # 마지막 봉 진행 중
    assert closed["signal_bar"] == rows[-1][0] and forming["signal_bar"] == rows[-2][0] and forming["forming"] is True
    assert forming["price"] == c[-1] and closed["price"] == c[-1]
    with pytest.raises(ValueError):
        ep.plan_timeframe([[T0, 2, 3, 4, 5]] * 200, step, 10)                                # 형식이 다르면 추측하지 않고 오류
    assert ep.plan_timeframe(rows[:50], step, 10)["status"] == "insufficient"


# ---------------------------------------------------------------- 대시보드가 읽는 JSON
def test_the_api_payload_is_json_safe_cached_and_carries_the_study_summary(tmp_path):
    import json
    from ena_bot import entry_api

    calls = []
    o, h, l, c = ohlc(path([(180, 0.004), (7, -0.006)]))
    rows = [[T0 + i * 3600_000, o[i], h[i], l[i], c[i], 1.0] for i in range(len(c))]

    def fetch(iv, n):
        calls.append(iv)
        return rows
    cache = {}
    a = entry_api.payload(fetch, tmp_path, "ENAUSDT", ttl=60, cache=cache)
    b = entry_api.payload(fetch, tmp_path, "ENAUSDT", ttl=60, cache=cache)
    assert len(calls) == 4 and a["cached"] is False and b["cached"] is True and a["study"] == {"ok": False}      # 두 번째는 시세를 다시 받지 않는다
    json.dumps(a, allow_nan=False)                                                                                # NaN이 남아 있으면 서버가 직렬화에 실패한다
    assert entry_api.clean({"x": [float("nan"), float("inf"), 1.5]}) == {"x": [None, None, 1.5]}
    (tmp_path / "entry_study.json").write_text(json.dumps({"generated": 1, "coins": ["A"], "groups": {"4h|pullback": {"n": 5, "verdict": {"use": False, "why": "표본 부족"}}}}))
    c2 = entry_api.payload(fetch, tmp_path, "ENAUSDT", ttl=60, cache=cache)
    assert c2["study"]["ok"] and c2["study"]["groups"]["4h|pullback"]["verdict"]["why"] == "표본 부족"            # 검증 요약은 캐시와 상관없이 최신 파일을 읽는다


# ---------------------------------------------------------------- 현실성 경고(손절 폭·비용·거리)
def test_costs_break_even_win_rate_and_distance_are_computed_from_the_plan():
    o, h, l, c = ohlc(path([(180, 0.004), (7, -0.006)], seed=6), seed=6)
    ind = ep.indicators(o, h, l, c)
    p = next(x for x in ep.candidates(ind, len(c) - 1, price=c[-1] * 1.01) if x["setup"] == "pullback")
    risk = abs(p["entry"] - p["stop"])
    assert p["cost_r"] == pytest.approx(p["entry"] * 2 * (ep.COST_FEE + ep.COST_SLIP) / risk)
    assert p["be_win_rate"] == pytest.approx((1 + p["cost_r"]) / (p["rr"] + 1))               # 비용을 뺀 뒤 본전이 되는 승률
    assert p["dist_pct"] == pytest.approx((p["entry"] - p["price"]) / p["price"])              # 진입가가 현재가보다 몇 % 위(+)/아래(-)인지
    assert isinstance(p["warnings"], list) and isinstance(p["severe"], bool)


def test_a_plan_whose_stop_would_be_far_away_is_refused_instead_of_shown_with_unrealistic_prices():
    o, h, l, c = ohlc(path([(180, -0.006), (7, 0.008)], noise=0.015, seed=1), seed=1, spread=0.015)           # 변동이 큰 종목
    h[-4] *= 1.1                                                        # 최근 10봉 안의 장대 고점이 손절 위치를 끌어올린다
    ind = ep.indicators(o, h, l, c)
    assert [x for x in ep.candidates(ind, len(c) - 1) if x["setup"] == "pullback"] == []                      # 손절이 2.5 ATR·8%를 넘으면 계획을 만들지 않는다
    rows = [[T0 + i * 3600_000, o[i], h[i], l[i], c[i], 1.0] for i in range(len(c))]
    res = ep.plans(lambda iv, n: rows, T0 + len(c) * 3600_000)
    for tf in res["timeframes"].values():
        assert not any(x["setup"] == "pullback" and x["risk_pct"] > ep.MAX_STOP_PCT for x in tf["all"])


def checks3(n_ok):
    return [("a", i < n_ok, "") for i in range(3)]


def test_finish_refuses_wide_stops_warns_on_moderate_ones_and_caps_far_targets():
    kw = dict(zone=(99.0, 101.0), checks=checks3(3), state="in_zone", trend=1, price=100.0, notes=[])
    assert ep._finish(1, "pullback", 100.0, 91.0, targets=[115.0, 130.0], a=4.0, **kw) is None              # 손절 폭 9% > 8%
    assert ep._finish(1, "pullback", 100.0, 99.0, targets=[101.5, 103.0], a=2.0, **kw) is None              # 손절 폭이 0.8 ATR(1.6)보다 좁다
    p = ep._finish(1, "pullback", 100.0, 93.0, targets=[110.5, 121.0], a=3.5, **kw)                          # 손절 폭 7%(2 ATR): 허용하되 경고
    assert p is not None and any("손절 폭이 큼" in w for w in p["warnings"]) and p["severe"] is False
    assert p["targets"] == [pytest.approx(100.0 + 2.5 * 3.5), pytest.approx(100.0 + min(21.0, 4.5 * 3.5))]     # 첫 목표 8.75, 둘째 15.75(ATR 제한)
    assert p["rr"] == pytest.approx(8.75 / 7.0) and p["rr"] >= ep.MIN_RR and p["state"] == "in_zone"
    poor = ep._finish(1, "pullback", 100.0, 95.0, targets=[107.5, 115.0], a=2.0, **kw)                      # 위험 5, 첫 목표가 2.5 ATR(5)로 제한 → 손익비 1.0 < 1.2
    assert poor["rr"] == pytest.approx(1.0) and poor["state"] == "poor_rr"
    weak = ep._finish(1, "pullback", 100.0, 98.0, targets=[103.0, 106.0], a=2.0, **{**kw, "checks": checks3(1)})
    assert weak["state"] == "weak" and weak["score"] == 1


def test_a_very_tight_stop_makes_costs_a_large_part_of_the_risk():
    kw = dict(zone=(99.5, 100.2), checks=checks3(3), state="in_zone", trend=1, price=100.0, notes=[])
    p = ep._finish(1, "pullback", 100.0, 99.6, targets=[100.6, 101.2], a=0.4, **kw)                         # 위험 0.4(0.4%)
    assert p["cost_r"] == pytest.approx(100.0 * 2 * (ep.COST_FEE + ep.COST_SLIP) / 0.4) and p["cost_r"] > ep.SEVERE_COST_R
    assert any("수수료·슬리피지" in w for w in p["warnings"]) and p["severe"] is True
