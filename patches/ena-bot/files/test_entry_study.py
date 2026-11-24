import json
import math
import random
from types import SimpleNamespace

import numpy as np
import pytest

from ena_bot import entry_study as es

COST = (es.FEE + es.SLIP)


def plan(direction="long", entry=100.0, stop=98.0, targets=(103.0, 106.0)):
    return {"direction": direction, "entry": entry, "stop": stop, "targets": list(targets)}


def bars(rows):
    """(시가, 고가, 저가, 종가) 행들."""
    return [list(map(float, col)) for col in zip(*rows)]


def test_a_long_trade_that_fills_then_reaches_the_target_earns_1_5r_minus_costs():
    o, h, l, c = bars([(100, 100, 100, 100), (102, 102, 101, 101.5), (101, 101.5, 99.5, 101), (101, 103.5, 100.5, 103), (103, 104, 102, 103)])
    r = es.fill_and_exit(plan(), o, h, l, c, 0)
    assert r["outcome"] == "win" and r["entry_i"] == 2 and r["exit_i"] == 3
    assert r["R"] == pytest.approx(1.5 - (100 + 103) * COST / 2.0)


def test_gap_through_the_limit_fills_at_the_open_and_stop_wins_a_same_bar_tie():
    o, h, l, c = bars([(100, 100, 100, 100), (99, 99.5, 98.5, 99), (99, 104, 97.5, 100)])
    r = es.fill_and_exit(plan(), o, h, l, c, 0)
    assert r["entry_i"] == 1 and r["outcome"] == "loss" and r["exit_i"] == 2                     # 시가 99에 체결, 다음 봉에서 손절·목표가 함께 닿으면 손절
    assert r["R"] == pytest.approx((98 - 99) / 1.0 - (99 + 98) * COST / 1.0)


def test_stop_touched_on_the_fill_bar_itself_is_a_loss_and_no_touch_means_no_trade():
    o, h, l, c = bars([(100, 100, 100, 100), (100, 100.5, 97.0, 98)])
    r = es.fill_and_exit(plan(), o, h, l, c, 0)
    assert r["outcome"] == "loss" and r["entry_i"] == r["exit_i"] == 1 and r["R"] == pytest.approx(-1 - (100 + 98) * COST / 2.0)
    far = bars([(100, 100, 100, 100)] + [(105, 106, 104.5, 105)] * 10)
    assert es.fill_and_exit(plan(), *far, 0) is None                                               # wait_bars 안에 닿지 않음


def test_timeout_exits_at_the_close_and_a_short_is_the_mirror_image():
    rows = [(100, 100, 100, 100), (100.5, 100.6, 99.8, 100.2)] + [(100.2, 100.8, 99.6, 100.4)] * 60
    o, h, l, c = bars(rows)
    r = es.fill_and_exit(plan(), o, h, l, c, 0, hold=10)
    assert r["outcome"] == "timeout" and r["exit_i"] == 11 and r["R"] == pytest.approx((c[11] - 100) / 2.0 - (100 + c[11]) * COST / 2.0)
    o, h, l, c = bars([(100, 100, 100, 100), (99, 100.5, 98.5, 99), (99, 99.5, 96.5, 97)])
    s = es.fill_and_exit(plan("short", 100.0, 102.0, (97.0, 94.0)), o, h, l, c, 0)
    assert s["outcome"] == "win" and s["R"] == pytest.approx(1.5 - (100 + 97) * COST / 2.0)


def walk(seed, n, vol=0.006):
    rnd = random.Random(seed)
    c = [1.0]
    for _ in range(n):
        c.append(c[-1] * math.exp(rnd.gauss(0, vol)))
    o = [c[0]] + c[:-1]
    h = [max(a, b) * (1 + abs(rnd.gauss(0, 0.002))) for a, b in zip(o, c)]
    l = [min(a, b) * (1 - abs(rnd.gauss(0, 0.002))) for a, b in zip(o, c)]
    return o, h, l, c


def test_simulation_trades_never_overlap_and_only_follow_a_signal():
    o, h, l, c = walk(3, 3000)
    res = es.simulate(o, h, l, c, [i * 3600_000 for i in range(len(c))])
    tr = res["trades"]
    assert tr and len(tr) <= res["signals"]
    for a, b in zip(tr, tr[1:]):
        assert b["entry_i"] > a["exit_i"]                      # 거래가 열려 있는 동안의 신호는 건너뛴다
    assert all(t["entry_i"] >= es.ep.MIN_BARS + 1 and t["setup"] in ("pullback", "reversion") for t in tr)


def coin(seed, n=6000):
    o, h, l, c = walk(seed, n)
    return SimpleNamespace(ts=np.arange(n + 1, dtype=np.int64) * 3600_000 + 1_700_000_000_000, open=np.array(o), high=np.array(h), low=np.array(l), close=np.array(c), volume=np.ones(n + 1))


def test_study_over_random_walks_reports_every_group_and_never_claims_an_edge(tmp_path):
    pool = {f"C{k}": coin(k) for k in range(1, 9)}
    res = es.study(pool)
    assert set(res["groups"]) == {f"{tf}|{s}" for tf in ("1h", "4h", "1d") for s in ("pullback", "reversion")} and res["alpha"] == 0.05 / 6
    h1 = res["groups"]["1h|pullback"]
    assert h1["n"] > 50 and h1["n_clusters"] > 30 and h1["ci"][0] < h1["ci"][1]
    assert h1["mean"] < 0.1                                    # 무작위 가격에서는 비용을 빼면 이득이 없다
    assert all(g["verdict"]["use"] is False for g in res["groups"].values())
    assert "기준 미달" in h1["verdict"]["why"] or "표본 부족" in h1["verdict"]["why"]
    text = es.render(res)
    assert "다중 비교 보정" in text and "추세 눌림목" in text and "15분봉은 이력이 없어" in text and "생존 편향" in text
    (tmp_path / "entry_study.json").write_text(json.dumps(res, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    saved = es.load_saved(tmp_path)
    assert saved["ok"] and saved["coins"] == 8 and saved["groups"]["1h|pullback"]["verdict"]["use"] is False and saved["age_days"] < 1
    (tmp_path / "entry_study.json").write_text("{broken")
    assert es.load_saved(tmp_path) == {"ok": False}


def test_too_little_history_or_a_single_coin_is_insufficient():
    res = es.study({"ONE": coin(1, 3000)})
    g = res["groups"]["1h|pullback"]
    assert g["verdict"]["use"] is False and ("표본 부족" in g["verdict"]["why"] or g["n"] == 0)
    assert es.study({"X": coin(2, 100)})["groups"]["1h|pullback"]["verdict"]["why"] == "거래가 없습니다"
