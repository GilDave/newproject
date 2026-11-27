"""ENA 저장소 tests/ 로 복사해 쓰는 테스트(이 저장소의 테스트 러너에서는 실행되지 않는다)."""
import json
import sqlite3

from ena_bot import shadow_scorecard as sc

H4 = 4 * 3_600_000
T0 = 1_760_000_000_000


def _db(tmp_path, n=10, ml=True, halted_from=None, shadow_skip=None):
    p = tmp_path / "bot.db"
    c = sqlite3.connect(p); c.execute("create table events(id integer primary key, ts_ms int, kind text, data text)")
    def ev(kind, **d): c.execute("insert into events(ts_ms,kind,data) values(?,?,?)", (T0, kind, json.dumps(d)))
    for i in range(n):
        ts = T0 + i * H4
        price = 1.0 + i * 0.01
        why = ["halted:max_drawdown"] if halted_from is not None and i >= halted_from else []
        ev("trend_cycle", ts=ts, price=price, why=why, qty=10 if i else 0, delta=10 if i == 1 else 0, equity=1000 + i * 10)
        if not (shadow_skip is not None and i == shadow_skip):
            ev("trend_shadow", ts=ts, price=price, qty=5, delta=0, equity=1000 + i * 5)
        if ml:
            ev("ml_shadow", ts=ts, price=price, qty=0, delta=0, equity=1000 - i * 10)
    c.commit(); c.close()
    return p


def test_metrics_are_computed_over_the_same_window(tmp_path):
    r = sc.payload(_db(tmp_path))
    by = {a["key"]: a for a in r["accounts"]}
    assert r["ok"] and r["cycles"] == 10 and r["enough"] is False
    assert by["base"]["return_pct"] == 9.0 and by["shadow"]["return_pct"] == 4.5 and by["ml"]["return_pct"] == -9.0
    assert by["shadow"]["vs_base_pct"] == -4.5 and by["base"]["vs_base_pct"] is None
    assert by["ml"]["max_dd_pct"] == -9.0 and by["base"]["max_dd_pct"] == 0.0
    assert by["base"]["rebalances"] == 1 and by["ml"]["in_market_pct"] == 0.0
    assert r["buy_hold_pct"] == 9.0
    assert any("90일 미만" in n for n in r["notes"])
    assert by["base"]["curve"][0] == [T0, 100.0]


def test_only_cycles_present_in_every_account_are_used(tmp_path):
    r = sc.payload(_db(tmp_path, shadow_skip=4))
    assert r["cycles"] == 9


def test_halted_cycles_are_reported_and_missing_ml_is_noted(tmp_path):
    r = sc.payload(_db(tmp_path, ml=False, halted_from=7))
    assert r["halted_cycles"] == 3 and {a["key"] for a in r["accounts"]} == {"base", "shadow"}
    assert any("정지" in n for n in r["notes"]) and any("ML" in n for n in r["notes"])


def test_enough_only_after_min_days(tmp_path):
    r = sc.payload(_db(tmp_path, n=600))
    assert r["enough"] is True and r["days"] >= 90 and not any("90일 미만" in n for n in r["notes"])


def test_no_data_and_missing_db(tmp_path):
    assert sc.payload(tmp_path / "none.db")["ok"] is False
    p = tmp_path / "e.db"
    c = sqlite3.connect(p); c.execute("create table events(id integer primary key, ts_ms int, kind text, data text)"); c.commit(); c.close()
    r = sc.payload(p)
    assert r["ok"] and r["accounts"] == [] and r["enough"] is False
