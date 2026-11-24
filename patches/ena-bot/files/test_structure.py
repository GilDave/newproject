import math
import random

import pytest

from ena_bot import structure as st


def build(targets, flat=80, leg=45, noise=0.002, seed=1, tail=0):
    """목표 가격들을 차례로 지나는 가격 경로(종가)와 OHLC. 앞에 평평한 구간을 두어 ATR이 안정되게 한다."""
    rnd = random.Random(seed)
    closes = [targets[0] * (1 + rnd.gauss(0, noise)) for _ in range(flat)]
    marks = [len(closes) - 1]
    for a, b in zip(targets, targets[1:]):
        for k in range(1, leg + 1):
            closes.append(a + (b - a) * k / leg)
        marks.append(len(closes) - 1)
    closes = [x * (1 + rnd.gauss(0, noise)) for x in closes]
    o = [closes[0]] + closes[:-1]
    h = [max(a, b) * (1 + abs(rnd.gauss(0, noise)) * 0.8) for a, b in zip(o, closes)]
    l = [min(a, b) * (1 - abs(rnd.gauss(0, noise)) * 0.8) for a, b in zip(o, closes)]
    return o, h, l, closes, marks


UP = [1.00, 1.30, 1.12, 1.65, 1.45, 1.80, 1.55]            # 이상적인 상승 5파 + 조정 시작


def test_zigzag_alternates_and_pivots_sit_on_real_highs_and_lows():
    o, h, l, c, marks = build(UP)
    piv = st.zigzag(h, l, c, 3.0)
    assert all(a["kind"] != b["kind"] and b["i"] > a["i"] for a, b in zip(piv, piv[1:]))
    for p in piv:
        assert (h if p["kind"] == "H" else l)[p["i"]] == p["price"]
    last = piv[-7:]                                          # 앞의 평평한 잡음 구간에서 생기는 작은 스윙은 제외하고, 설계한 7개 전환점을 본다
    assert [p["kind"] for p in last] == ["L", "H", "L", "H", "L", "H", "L"]
    assert [p["provisional"] for p in last] == [False] * 6 + [True]
    for p, m in zip(last[1:], marks[1:7]):
        assert abs(p["i"] - m) <= 4                          # 설계한 전환점 근처에서 확정(첫 저점은 평평한 구간의 가장 낮은 곳이라 위치가 앞설 수 있다)


def test_a_clean_up_impulse_is_found_with_labels_rules_and_invalidation():
    o, h, l, c, marks = build(UP)
    res = st.analyze(o, h, l, c)["elliott"]
    assert res["status"] == "ok" and res["direction"] == "up" and res["complete"] is True
    assert [p["label"] for p in res["pivots"]] == ["0", "1", "2", "3", "4", "5"] and [p["kind"] for p in res["pivots"]] == list("LHLHLH")
    assert len(res["rules"]) == 4 and res["score"] >= 0.8 and "2파 되돌림 38~79%" in res["hits"]
    assert res["invalidation"]["side"] == "above" and res["invalidation"]["level"] == pytest.approx(1.80, abs=0.06)
    assert res["targets"] == []                              # 완성된 5파에는 5파 목표가 없다


def test_an_unfinished_fifth_wave_gets_an_invalidation_and_target_zone():
    o, h, l, c, marks = build(UP[:5])                         # 4파 저점에서 끝남
    res = st.analyze(o, h, l, c)["elliott"]
    assert res["status"] == "ok" and res["complete"] is False and res["direction"] == "up"
    assert [p["label"] for p in res["pivots"]] == ["0", "1", "2", "3", "4"]
    assert res["invalidation"]["side"] == "below" and res["invalidation"]["level"] == pytest.approx(1.30, abs=0.06)       # 1파 끝
    lo, hi = res["targets"]
    assert lo == pytest.approx(1.45 + 0.618 * 0.30, abs=0.06) and hi == pytest.approx(1.45 + 0.30, abs=0.06) and lo < hi
    assert "5파가 1파의 50~120%" not in res["hits"] + res["misses"]       # 5파가 없어 평가 항목이 아니다


def test_a_down_impulse_is_the_mirror_image():
    mirrored = [3 - x for x in UP]
    o, h, l, c, _ = build(mirrored)
    res = st.analyze(o, h, l, c)["elliott"]
    assert res["status"] == "ok" and res["direction"] == "down" and res["complete"] is True
    assert [p["kind"] for p in res["pivots"]] == list("HLHLHL")
    assert res["invalidation"]["side"] == "below" and res["invalidation"]["level"] == pytest.approx(3 - 1.80, abs=0.06)


def test_broken_rules_give_no_count_instead_of_a_forced_one():
    overlap = [1.00, 1.30, 1.12, 1.65, 1.20, 1.80, 1.55]      # 4파가 1파 영역(1.30)을 침범
    assert st.analyze(*build(overlap)[:4])["elliott"]["status"] == "none"
    short3 = [1.00, 1.30, 1.12, 1.30, 1.15, 1.80, 1.55]       # 처음부터 세면 3파가 1파 끝을 못 넘는다
    e = st.analyze(*build(short3)[:4])["elliott"]
    assert e["status"] == "none" or e["pivots"][0]["price"] > 1.1      # 앞쪽 전체를 1파로 세는 카운트는 없고, 뒤쪽 일부에서 시작하는 다른 카운트만 가능하다
    deep2 = [1.00, 1.30, 0.95, 1.65, 1.45, 1.80, 1.55]        # 2파가 1파 시작점을 넘어 되돌림
    assert st.analyze(*build(deep2)[:4])["elliott"]["status"] == "none"


def test_random_walks_never_break_the_invariants():
    for seed in range(1, 8):
        rnd = random.Random(seed)
        closes = [1.0]
        for _ in range(700):
            closes.append(closes[-1] * math.exp(rnd.gauss(0, 0.006)))
        o = [closes[0]] + closes[:-1]
        h = [max(a, b) * (1 + abs(rnd.gauss(0, 0.002))) for a, b in zip(o, closes)]
        l = [min(a, b) * (1 - abs(rnd.gauss(0, 0.002))) for a, b in zip(o, closes)]
        res = st.analyze(o, h, l, closes)
        piv = res["pivots"]
        assert all(a["kind"] != b["kind"] and b["i"] > a["i"] for a, b in zip(piv, piv[1:]))
        assert all(not p["provisional"] for p in piv[:-1])
        e = res["elliott"]
        assert e["status"] in ("ok", "none")
        if e["status"] == "ok":
            q = [p["price"] for p in e["pivots"]]
            up = e["direction"] == "up"
            s = [x if up else -x for x in q]
            assert s[2] > s[0] and s[3] > s[1] and s[4] > s[1]               # 필수 규칙은 항상 지켜진다
            if len(s) == 6:
                w1, w3, w5 = s[1] - s[0], s[3] - s[2], s[5] - s[4]
                assert not (w3 < w1 and w3 < w5)
        for line in res["trendlines"].values():
            if line:
                assert line["touches"] >= 2 and line["b"]["i"] > line["a"]["i"]


def test_support_line_through_rising_lows_and_a_broken_line_is_rejected():
    n = 300
    base = [1.0 + 0.002 * i for i in range(n)]                           # 이상적인 상승 지지선
    c = []
    for i in range(n):
        phase = i % 30
        wave = 0.06 * (1 - abs(phase - 15) / 15)                         # 저점이 선에 닿는 톱니
        c.append(base[i] * (1 + wave))
    o = [c[0]] + c[:-1]
    h = [max(a, b) * 1.002 for a, b in zip(o, c)]
    l = [min(a, b) * 0.998 for a, b in zip(o, c)]
    res = st.analyze(o, h, l, c)
    sup = res["trendlines"]["support"]
    assert sup is not None and sup["touches"] >= 3 and sup["slope"] == pytest.approx(0.002, rel=0.15)
    assert sup["last"] == pytest.approx(base[-1], rel=0.03)
    c2 = c[:]
    c2[-20] = base[-20] * 0.80                                           # 지지선을 크게 이탈한 종가: 그 선은 더 이상 지지선이 아니다
    o2 = [c2[0]] + c2[:-1]
    h2 = [max(a, b) * 1.002 for a, b in zip(o2, c2)]
    l2 = [min(a, b) * 0.998 for a, b in zip(o2, c2)]
    broken = st.analyze(o2, h2, l2, c2)["trendlines"]["support"]
    assert broken is None or broken["slope"] != pytest.approx(sup["slope"], rel=0.05) or broken["b"]["i"] != sup["b"]["i"]


def test_short_input_returns_empty_results():
    r = st.analyze([1.0] * 30, [1.01] * 30, [0.99] * 30, [1.0] * 30)
    assert r["pivots"] == [] and r["elliott"]["status"] == "none" and r["trendlines"] == {"support": None, "resistance": None}


def test_a_fifth_wave_whose_last_pivot_is_still_moving_is_in_progress_with_a_stable_invalidation():
    forming = UP[:5] + [1.62]                                # 5파가 오르는 중(아직 고점 확정 전)
    o, h, l, c, _ = build(forming)
    res = st.analyze(o, h, l, c)["elliott"]
    assert res["status"] == "ok" and res["direction"] == "up" and res["complete"] is False and res["forming5"] is True
    assert res["pivots"][-1]["provisional"] is True and [p["label"] for p in res["pivots"]] == ["0", "1", "2", "3", "4", "5"]
    assert res["invalidation"]["side"] == "below" and res["invalidation"]["level"] == pytest.approx(1.45, abs=0.06)       # 5파 시작점(4파 끝): 현재 고점이 아니다
    assert res["invalidation"]["level"] < res["pivots"][-1]["price"] - 0.1
    lo, hi = res["targets"]
    assert lo == pytest.approx(1.45 + 0.618 * 0.30, abs=0.06) and hi == pytest.approx(1.75, abs=0.06)
    down = st.analyze(*build([3 - x for x in forming])[:4])["elliott"]
    assert down["direction"] == "down" and down["forming5"] is True and down["invalidation"]["side"] == "above" and down["invalidation"]["level"] == pytest.approx(3 - 1.45, abs=0.06)
    done = st.analyze(*build(UP)[:4])["elliott"]                 # 5파가 확정되면 이전과 같다
    assert done["complete"] is True and done["forming5"] is False and done["invalidation"]["side"] == "above"
