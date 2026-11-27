"""ENA 저장소에서 실행하는 테스트(wire_econ_calendar.py 적용 후 tests/ 로 복사)."""
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ena_bot import econ_calendar as ec
from ena_bot.dashboard_market_context import fetch_calendar

PAGE = Path(__file__).resolve().parents[1] / "ena_bot/dashboard_static/index.html"
FEED = json.dumps([
    {"title": "Prelim Wholesale Inventories m/m", "country": "USD", "date": "2026-11-27T08:30:00-05:00", "impact": "Low", "forecast": "0.2%", "previous": "0.1%"},
    {"title": "Retail Inventories ex Auto m/m", "country": "USD", "date": "2026-11-27T08:30:00-05:00", "impact": "Low", "forecast": "0.3%", "previous": "0.2%", "actual": "0.4%"},
    {"title": "Core PCE Price Index m/m", "country": "USD", "date": "2026-12-04T08:30:00-05:00", "impact": "High", "forecast": "0.2%", "previous": "0.3%"},
    {"title": "CPI m/m", "country": "USD", "date": "2026-12-10T08:30:00-05:00", "impact": "High", "forecast": "0.3%", "previous": "0.2%"},
    {"title": "Non-Farm Employment Change", "country": "USD", "date": "2026-12-04T08:30:00-05:00", "impact": "High", "forecast": "150K", "previous": "140K"},
    {"title": "German Ifo", "country": "EUR", "date": "2026-11-27T04:00:00-05:00", "impact": "Medium"},
    {"title": "", "country": "USD", "date": "x"}, "garbage",
])
NOW = int(datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)         # 한국 시간 11/27 21:00


def test_parse_feed_keeps_usd_items_normalizes_and_translates():
    ev = ec.parse_feed(FEED)
    assert [e["name"] for e in ev][:2] == ["도매재고 전월비", "소매재고 전월비 (자동차 제외)"]
    assert all(e["country"] == "USD" for e in ev) and len(ev) == 5                        # 유로·깨진 항목 제외
    w = ev[0]
    assert (w["previous"], w["forecast"], w["actual"], w["impact"]) == ("0.1%", "0.2%", "", "low")
    assert ev[1]["actual"] == "0.4%"
    assert w["ts"] == int(datetime(2026, 11, 27, 13, 30, tzinfo=timezone.utc).timestamp() * 1000)   # 08:30 ET = 13:30 UTC = 22:30 KST
    with pytest.raises(ValueError):
        ec.parse_feed("{}")
    with pytest.raises(ValueError):
        ec.parse_feed(json.dumps([{"title": "x", "country": "EUR", "date": "2026-01-01T00:00:00Z"}]))


@pytest.mark.parametrize("title,ko", [("Core PCE Price Index m/m", "근원 PCE 물가"), ("Unemployment Claims", "신규 실업수당 청구건수"), ("Non-Farm Employment Change", "비농업 고용(NFP)"),
                                        ("Retail Sales m/m", "소매판매 전월비"), ("ISM Manufacturing PMI", "ISM 제조업 PMI"), ("Some Unknown Indicator", "Some Unknown Indicator")])
def test_korean_names_with_english_fallback(title, ko):
    assert ec.ko_name(title) == ko


def test_collector_refresh_writes_cache_and_failure_keeps_the_old_one(tmp_path):
    calls = []
    def ok(url):
        calls.append(url)
        return FEED
    c = ec.Collector(tmp_path, fetch=ok, clock=lambda: NOW / 1000, network=True)
    r = c.refresh()
    assert r["ok"] and r["count"] == 5 and c.error == ""
    assert json.loads(c.cache_path.read_text(encoding="utf-8"))["fetched_at"] == NOW
    def boom(url):
        raise OSError("blocked")
    c2 = ec.Collector(tmp_path, fetch=boom, clock=lambda: (NOW + 5000) / 1000, network=True)
    r2 = c2.refresh()
    assert not r2["ok"] and "blocked" in c2.error
    assert len(c2.load_cache()["events"]) == 5                                            # 실패해도 마지막 캐시는 남는다


def test_due_schedule_is_gentle_normally_and_closer_to_releases(tmp_path):
    c = ec.Collector(tmp_path, fetch=lambda u: FEED, clock=lambda: NOW / 1000, network=True)
    assert c.due(NOW)                                                                      # 캐시가 없으면 받는다
    c.refresh()
    assert not c.due(NOW + 30 * 60_000)                                                    # 평소: 30분 뒤엔 아직 받지 않는다(발표와 먼 시각)
    assert c.due(NOW + 61 * 60_000)                                                        # 1시간이 지나면 받는다
    release = int(datetime(2026, 11, 27, 13, 30, tzinfo=timezone.utc).timestamp() * 1000)
    assert c.due(release + 11 * 60_000) is True                                            # 발표 직후엔 10분이 지나면 다시 받는다(발표값 채우기)
    c.error, c._last_try = "x", (release + 11 * 60_000) / 1000
    assert c.due(release + 12 * 60_000) is False                                           # 실패한 직후엔 잠시 쉰다


def test_network_is_off_during_tests_unless_explicitly_enabled(tmp_path):
    c = ec.Collector(tmp_path, fetch=lambda u: (_ for _ in ()).throw(AssertionError("네트워크를 쓰면 안 된다")))
    assert c.network is False
    c.poke()                                                                               # 호출해도 아무 일도 없다


def test_manual_entries_fill_in_and_cli_roundtrip(tmp_path, capsys):
    assert ec.main(["--data", str(tmp_path), "add", "도매재고 전월비", "2026-11-27 22:30", "--previous", "0.1%", "--forecast", "0.2%"]) == 0
    assert ec.main(["--data", str(tmp_path), "actual", "도매재고 전월비", "2026-11-27 22:30", "0.5%"]) == 0
    c = ec.Collector(tmp_path, network=False)
    m = c.manual()
    assert len(m) == 1 and m[0]["actual"] == "0.5%" and m[0]["manual"] is True and m[0]["ts"] == int(datetime(2026, 11, 27, 13, 30, tzinfo=timezone.utc).timestamp() * 1000)
    merged = ec.merge(ec.parse_feed(FEED), m)
    w = [e for e in merged if e["name"] == "도매재고 전월비"]
    assert len(w) == 1 and w[0]["actual"] == "0.5%" and w[0]["forecast"] == "0.2%" and w[0]["manual"] is True      # 피드 항목과 합쳐져 하나가 되고 발표값이 채워진다
    assert ec.main(["--data", str(tmp_path), "actual", "없는 지표", "2026-11-27 22:30", "1"]) == 1


def test_calendar_plus_merges_without_losing_static_data_or_duplicating_it(tmp_path):
    c = ec.Collector(tmp_path, fetch=lambda u: FEED, clock=lambda: NOW / 1000, network=True)
    c.refresh()
    base = fetch_calendar(NOW)
    out = ec.fetch_calendar_plus(NOW, base, collector=c)
    assert out["coverage"] == base["coverage"] and len(out["events"]) >= len(base["events"])
    names = [e["name"] for e in out["events"][len(base["events"]):]]
    assert "근원 PCE 물가" in names and "소비자물가(CPI)" not in names                      # 높음 중요도는 격자에, 기존 CPI 와 겹치는 것은 중복 표시하지 않는다
    assert "비농업 고용(NFP)" in names                                                      # events.py 에 없는 12월 고용보고서는 외부 피드로 채운다
    assert out["status"] == fetch_calendar(NOW)["status"] or out["status"] == "ok"
    assert "도매재고 전월비" not in names                                                    # 낮음 중요도는 격자에 넣지 않는다(오늘의 지표 목록에는 있다)
    assert [e["name"] for e in out["today"]] == ["도매재고 전월비", "소매재고 전월비 (자동차 제외)"]
    assert out["today"][1]["actual"] == "0.4%"
    assert out["econ"]["fetched_at"] == NOW and out["econ"]["count"] == 5 and out["updated_at"] == NOW   # 신선도 배지가 실제 수집 시각을 쓴다
    assert all(out["items"][i]["ts"] <= out["items"][i + 1]["ts"] for i in range(len(out["items"]) - 1))
    json.dumps(out)


def test_calendar_plus_without_any_cache_keeps_working_and_says_so(tmp_path):
    c = ec.Collector(tmp_path, fetch=lambda u: (_ for _ in ()).throw(OSError("blocked")), clock=lambda: NOW / 1000, network=True)
    c.refresh()
    out = ec.fetch_calendar_plus(NOW, fetch_calendar(NOW), collector=c)
    assert out["today"] == [] and out["econ"]["fetched_at"] is None and "받지 못했습니다" in out["econ"]["note"]
    assert out["events"] and out["status"] == fetch_calendar(NOW)["status"]                    # 기본 일정과 상태는 그대로


def test_page_renders_today_block_in_node(tmp_path):
    html = PAGE.read_text(encoding="utf-8")
    m = re.search(r"/\* ECON-TODAY-START.*?/\* ECON-TODAY-END \*/\n", html, re.S)
    script = tmp_path / "econ.js"
    script.write_text("""
const escH = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const kstHM = ms => '22:30', kst = ms => '2026. 11. 27. 21:00:00';
""" + m.group(0) + """
const now = Date.now();
const c = {econ: {fetched_at: now, count: 4, manual: 1, error: 'curl 종료 코드 28'},
  today: [{ts: now - 1000, name: '도매재고 전월비', impact: 'low', previous: '0.1%', forecast: '0.2%', actual: '0.5%', manual: true},
          {ts: now - 500, name: '소매재고 전월비', impact: 'low', previous: '0.2%', forecast: '', actual: '', manual: false}],
  upcoming_econ: [{ts: now + 86400000, name: '근원 PCE <b>물가</b>', impact: 'high', previous: '', forecast: '0.2%', actual: '', manual: false}]};
const h = econTodayHtml(c);
console.log(JSON.stringify({h, empty: econTodayHtml({econ: {fetched_at: null, note: '받지 못했습니다'}, today: []}), none: econTodayHtml({})}));
""")
    res = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    h = out["h"]
    assert "오늘의 지표 발표" in h and "도매재고 전월비" in h and "<b>0.5%</b>" in h and "(수동)" in h
    assert "발표 <i>발표 대기</i>" in h and "이전 0.2% · 예상 —" in h                         # 아직 발표값이 없으면 '발표 대기'
    assert "이번 주 남은 일정" in h and "&lt;b&gt;" in h and "<b>물가" not in h              # 외부 문자열은 이스케이프된다
    assert "최근 갱신 실패: curl 종료 코드 28" in h and "수동 1건" in h
    assert "받지 못했습니다" in out["empty"] and "오늘 수집된 발표 일정이 없습니다" in out["empty"]
    assert out["none"] == ""


def test_exhausted_static_kinds_are_cleared_when_the_feed_covers_them(tmp_path):
    base = fetch_calendar(NOW)
    assert "NFP" in base.get("stale_kinds", [])                                            # 시험의 전제: 기본 일정의 NFP 가 소진돼 있다
    c = ec.Collector(tmp_path, fetch=lambda u: FEED, clock=lambda: NOW / 1000, network=True)
    c.refresh()
    out = ec.fetch_calendar_plus(NOW, base, collector=c)
    assert "NFP" not in out["stale_kinds"]
    assert out["status"] == "ok" and out["error"] is None
    assert next(k for k in out["kinds"] if k["kind"] == "NFP")["has_future"] is True
    c2 = ec.Collector(tmp_path / "other", fetch=lambda u: (_ for _ in ()).throw(OSError("x")), clock=lambda: NOW / 1000, network=True)
    c2.refresh()
    assert ec.fetch_calendar_plus(NOW, base, collector=c2)["status"] == base["status"]          # 피드가 없으면 기본 경고는 그대로
