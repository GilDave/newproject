"""ENA 저장소에서 실행하는 테스트(econ_reaction.py 적용 후 tests/ 로 복사)."""
import json
from datetime import datetime, timezone

from ena_bot import econ_calendar as ec
from ena_bot import econ_reaction as er

MIN = 60_000
T = int(datetime(2026, 11, 27, 13, 30, tzinfo=timezone.utc).timestamp() * 1000)   # 금요일 08:30 ET


def make_fetch(move=0.01, hole=False, missing_before=()):
    """T 시각 시가 100, 이후 60분 동안 move 만큼 직선으로 오르는 1분봉. 다른 날은 평소(0.1%)."""
    def fetch(sym, end_ms, limit):
        t0 = (end_ms - 61 * MIN) // MIN * MIN
        big = abs(t0 - T) < MIN
        if t0 in missing_before:
            return []
        rows = []
        for k in range(-5, 61):
            if hole and big and 10 <= k <= 40:
                continue
            frac = (k / 60) * (move if big else 0.001)
            p = 100 * (1 + max(frac, 0)) if k >= 0 else 100
            rows.append([t0 + k * MIN, p, p * 1.0005, p * 0.9995, p, 1.0])
        return rows
    return fetch


def test_reaction_returns_and_range():
    r = er.compute(make_fetch(0.01), "ENAUSDT", T)
    assert abs(r["ret60"] - 0.01) < 1e-3 and abs(r["ret15"] - 0.0025) < 1e-3
    assert r["ratio_range"] > 3 and r["n_base"] == 5


def test_missing_data_returns_none_not_garbage():
    assert er.compute(make_fetch(hole=True), "ENAUSDT", T) is None
    assert er.reaction_from_rows([], T) is None


def test_baseline_skips_weekends():
    for t in er.baseline_times(T):
        assert datetime.fromtimestamp(t / 1000, timezone.utc).weekday() < 5


def test_surprise_parsing():
    assert er.parse_num("0.5%") == 0.5 and er.parse_num("-1.2K") == -1200 and er.parse_num("") is None and er.parse_num("abc") is None
    assert abs(er.surprise("0.4%", "0.2%") - 0.2) < 1e-9 and er.surprise("", "0.2%") is None


def ev(ts=T, name_en="CPI m/m"):
    return {"ts": ts, "name": "소비자물가", "name_en": name_en, "impact": "high", "previous": "0.2%", "forecast": "0.3%", "actual": "0.4%"}


def test_record_waits_for_window_dedupes_and_survives_failures(tmp_path):
    store = er.ReactionStore(tmp_path / "r.jsonl")
    assert er.record_new([ev()], T + 30 * MIN, make_fetch(), store) == []                # 창이 아직 안 닫힘
    assert len(er.record_new([ev()], T + 65 * MIN, make_fetch(), store)) == 1
    assert er.record_new([ev()], T + 70 * MIN, make_fetch(), store) == []                # 중복 없음
    assert len(store.path.read_text().splitlines()) == 1
    assert er.record_new([ev(T - 9 * 86_400_000)], T + 70 * MIN, make_fetch(), store) == []   # 너무 오래됨
    def boom(*a):
        raise OSError("net")
    store2 = er.ReactionStore(tmp_path / "r2.jsonl")
    assert er.record_new([ev()], T + 65 * MIN, boom, store2) == [] and not store2.path.exists()   # 실패하면 기록하지 않고 다음에 재시도
    rec = json.loads(store.path.read_text().splitlines()[0])
    assert rec["surprise"] and abs(rec["surprise"] - 0.1) < 1e-9 and rec["btc"]


def test_corrupt_store_lines_are_ignored(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text("garbage\n{\"ts\": 1}\n", encoding="utf-8")
    assert er.ReactionStore(p).load() == {}


def test_collector_records_in_background_and_calendar_attaches_it(tmp_path):
    import time
    feed = json.dumps([{"title": "CPI m/m", "country": "USD", "date": "2026-11-27T08:30:00-05:00", "impact": "High", "forecast": "0.3%", "previous": "0.2%", "actual": "0.4%"}])
    now = T + 70 * MIN
    c = ec.Collector(tmp_path, fetch=lambda u: feed, clock=lambda: now / 1000, network=True, reaction_fetch=make_fetch())
    c.poke()
    c._thread.join(5)
    assert len(c.reactions.load()) == 1
    out = ec.fetch_calendar_plus(now, {"items": [], "updated_at": now}, collector=c)
    rows = [e for e in out["today"] if e["name_en"] == "CPI m/m"]
    assert rows and rows[0]["reaction"]["ena"]["ret15"] is not None and out["econ"]["reactions"] == 1


def test_late_actual_is_filled_into_existing_record(tmp_path):
    store = er.ReactionStore(tmp_path / "r.jsonl")
    e = ev(); e["actual"] = ""
    er.record_new([e], T + 65 * MIN, make_fetch(), store)
    assert json.loads(store.path.read_text().splitlines()[0])["surprise"] is None
    assert store.fill_actuals([ev()]) == 1
    assert abs(json.loads(store.path.read_text().splitlines()[0])["surprise"] - 0.1) < 1e-9
    assert store.fill_actuals([ev()]) == 0


def test_polls_every_two_minutes_while_actual_is_missing(tmp_path):
    feed = json.dumps([{"title": "CPI m/m", "country": "USD", "date": "2026-11-27T08:30:00-05:00", "impact": "High", "forecast": "0.3%", "previous": "0.2%"}])
    now = [T + 10 * MIN]
    c = ec.Collector(tmp_path, fetch=lambda u: feed, clock=lambda: now[0] / 1000, network=True)
    c.refresh()
    now[0] += 3 * MIN
    assert c.due(now[0])
    c2 = ec.Collector(tmp_path, fetch=lambda u: feed.replace('"previous"', '"actual": "0.4%", "previous"'), clock=lambda: now[0] / 1000, network=True)
    c2.refresh()
    now[0] += 3 * MIN
    assert not c2.due(now[0])
