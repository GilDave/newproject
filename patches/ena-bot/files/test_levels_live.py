import math
import random

import pytest

from ena_bot import levels_live as ll

NOW = 1_800_000_000_000


def rows(n, minutes, base=0.1, amp=0.03, seed=1, end=None):
    """사인파 + 잡음 시세. 마지막 봉은 진행 중(end 기준)으로 만든다."""
    rnd = random.Random(seed)
    step = minutes * 60_000
    end = NOW if end is None else end
    out, prev = [], base
    for i in range(n):
        ts = end - (n - i) * step + step // 2 - step // 2
        ts = (end // step - (n - 1 - i)) * step
        c = base * (1 + amp * math.sin(i / 9) + rnd.uniform(-0.004, 0.004))
        h, l = max(prev, c) * 1.004, min(prev, c) * 0.996
        out.append([ts, prev, h, l, c, rnd.uniform(500, 1500)])
        prev = c
    return out


def fetch_all(**kw):
    m = {"15": 15, "60": 60, "240": 240, "D": 1440}
    return lambda iv, n: rows(n, m[iv], **kw)


def test_live_levels_use_the_current_price_not_a_stored_one():
    body = ll.compute(fetch_all(), "ENAUSDT", NOW)
    r15 = fetch_all()("15", 200)
    assert body["live"] is True and body["as_of"] == NOW and body["current_price"] == pytest.approx(r15[-1][4])
    assert set(body["timeframes"]) == {"15m", "1h", "4h", "1d"}


def test_every_shown_plan_is_realistic():
    body = ll.compute(fetch_all(), "ENAUSDT", NOW)
    px = body["current_price"]
    shown = 0
    for tf, t in body["timeframes"].items():
        for side in ("long", "short"):
            p = t.get(side)
            if not p:
                assert t["why"].get(side) or t["status"] != "ok"
                continue
            shown += 1
            atr = t["atr"]
            risk = abs(p["entry"] - p["stop"])
            assert abs(p["entry"] - px) <= ll.MAX_ENTRY_ATR * atr + 1e-12
            assert ll.MIN_STOP_ATR * atr - 1e-12 <= risk <= ll.MAX_STOP_ATR * atr + 1e-12 and risk <= ll.MAX_STOP_PCT * p["entry"] + 1e-12
            assert abs(p["take_profit"] - p["entry"]) <= ll.TP_MAX_ATR * atr + 1e-12
            if side == "long":
                assert p["stop"] < p["entry"] < p["take_profit"]
            else:
                assert p["take_profit"] < p["entry"] < p["stop"]
    assert shown >= 1


def test_side_refuses_far_entries_wide_stops_and_caps_targets():
    near = {"price": 90.0, "low": 89.0, "high": 91.0}
    far = {"price": 140.0, "low": 139.0, "high": 141.0}
    p, why = ll._side("long", 100.0, 2.0, near, far)           # 진입가가 현재가에서 10 = 5 ATR
    assert p is None and "무효" in why
    p, why = ll._side("long", 100.0, 4.0, near, far)           # 10 = 2.5 ATR 안: 허용, 손절은 한 구간 폭(2) 아래지만 최소 0.8 ATR(3.2)로 넓힘
    assert p and p["stop"] == pytest.approx(90.0 - 3.2) and p["take_profit"] == pytest.approx(90.0 + 4.5 * 4.0) and p["capped"] is True
    assert p["rr"] == pytest.approx(18.0 / 3.2)
    wide = {"price": 98.0, "low": 90.0, "high": 106.0}         # 구간 폭 16 → 손절 폭 큼
    p, why = ll._side("long", 100.0, 2.0, wide, far)
    assert p is None and "넓어" in why
    s, why = ll._side("short", 100.0, 4.0, {"price": 108.0, "low": 107.0, "high": 109.0}, {"price": 60.0, "low": 59.0, "high": 61.0})
    assert s and s["take_profit"] == pytest.approx(108.0 - 18.0) and s["stop"] > s["entry"] > 100.0


def test_a_stuck_candle_file_cannot_make_the_base_price_old():
    fallback_called = []
    body = ll.payload(fetch_all(), lambda: fallback_called.append(1) or {}, "ENAUSDT", NOW, cache={})
    assert not fallback_called and body["live"] and "stale" not in body


def test_falls_back_to_stored_candles_and_marks_it_stale_when_live_prices_fail():
    def boom(iv, n):
        raise RuntimeError("rate limit")
    stored = {"current_price": 0.1176, "as_of": 1, "timeframes": {}, "disclaimer": "x"}
    body = ll.payload(boom, lambda: dict(stored), "ENAUSDT", NOW, cache={})
    assert body["stale"] is True and body["live"] is False and body["current_price"] == 0.1176


def test_one_failing_timeframe_does_not_block_the_others():
    good = fetch_all()

    def f(iv, n):
        if iv == "240":
            raise RuntimeError("x")
        return good(iv, n)
    body = ll.compute(f, "ENAUSDT", NOW)
    assert set(body["timeframes"]) == {"15m", "1h", "1d"}


def test_cache_and_nan_safety():
    calls = []

    def f(iv, n):
        calls.append(iv)
        return fetch_all()(iv, n)
    cache = {}
    a = ll.payload(f, lambda: {}, "X", NOW, ttl=60, cache=cache)
    n = len(calls)
    assert ll.payload(f, lambda: {}, "X", NOW, ttl=60, cache=cache) is a and len(calls) == n
    import json
    json.dumps(ll.compute(lambda iv, n: [], "X", NOW) if False else a, allow_nan=False)


def test_too_few_bars_gives_insufficient_and_bad_rows_are_ignored():
    t = ll.plan_timeframe(rows(5, 15), 15, NOW)
    assert t["status"] == "insufficient_data" and t["long"] is None
    messy = rows(60, 15) + [["x", 1], None, [1, 2, 3]]
    assert ll.plan_timeframe([r for r in messy if r], 15, NOW)["status"] in ("ok", "insufficient_data")


def test_price_at_the_bottom_of_the_range_explains_which_side_is_missing():
    base = rows(200, 60, base=0.1, amp=0.0, seed=3)
    for r in base:                                              # 가격이 계속 내려 마지막 봉이 범위의 최저점
        pass
    n = len(base)
    drift = [[r[0], r[1], r[2], r[3], r[4], r[5]] for r in base]
    for i, r in enumerate(drift):
        f = 1 - 0.25 * i / n
        drift[i] = [r[0], r[1] * f, r[2] * f, r[3] * f, r[4] * f, r[5]]
    t = ll.plan_timeframe(drift, 60, NOW)
    assert t["status"] == "ok" and t["long"] is None and t["short"] is None
    assert "지지 구간이 없음" in t["why"]["long"] and "지지 구간이 없음" in t["why"]["short"]


def test_reference_zones_are_kept_even_when_no_plan_can_be_made():
    base = rows(200, 60, base=0.1, amp=0.0, seed=3)
    n = len(base)
    drift = []
    for i, r in enumerate(base):
        f = 1 - 0.25 * i / n
        drift.append([r[0], r[1] * f, r[2] * f, r[3] * f, r[4] * f, r[5]])
    t = ll.plan_timeframe(drift, 60, NOW)
    assert t["long"] is None and t["short"] is None
    refs = t["refs"]
    assert refs["support"] == [] or all(z["dist_pct"] < 0 for z in refs["support"])
    assert all(z["dist_pct"] > 0 for z in refs["resistance"]) and len(refs["resistance"]) <= ll.REF_COUNT
    xs = [z["dist_pct"] for z in refs["resistance"]]
    assert xs == sorted(xs)                                     # 가까운 순서
    body = ll.compute(fetch_all(), "ENAUSDT", NOW)
    assert all("refs" in v for v in body["timeframes"].values() if v["status"] == "ok")
