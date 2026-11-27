"""ENA 저장소에서 실행(us_market.py 를 ena_bot/ 에 두고 wire_us_market.py 적용 후 tests/ 로 복사)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ena_bot import us_market as um

ROOT = Path(__file__).resolve().parents[1]


def yahoo(last=100.0, prev=101.0, n=30, drop_last=0.6):
    ts = [1_700_000_000 + 300 * i for i in range(n)]
    close = [100.0] * (n - 1) + [100.0 * (1 - drop_last / 100)]
    return json.dumps({"chart": {"result": [{"meta": {"regularMarketPrice": last, "chartPreviousClose": prev, "regularMarketTime": ts[-1]}, "timestamp": ts, "indicators": {"quote": [{"close": close}]}}]}})


def test_parse_yahoo_gives_day_and_one_hour_change():
    r = um.parse_yahoo(yahoo(last=99.0, prev=100.0), "QQQ")
    assert abs(r["pct_day"] + 1.0) < 1e-9 and r["pct_1h"] is not None and abs(r["pct_1h"] + 0.6) < 1e-6 and r["source"] == "yahoo"
    for bad in ("{}", json.dumps({"chart": {"result": None}}), "not json"):
        with pytest.raises(Exception):
            um.parse_yahoo(bad, "QQQ")


def test_parse_stooq_and_garbage():
    r = um.parse_stooq("Symbol,Date,Time,Open,High,Low,Close,Volume\nQQQ.US,2026-11-27,22:00:00,500,505,498,495,1000\n", "QQQ")
    assert abs(r["pct_day"] + 1.0) < 1e-9
    with pytest.raises(ValueError):
        um.parse_stooq("Symbol,Date\nQQQ,N/D\n", "QQQ")


def test_falls_back_to_second_user_agent_then_stooq_and_keeps_old_values_on_failure(tmp_path):
    calls = []
    def fetch(url, ua=None):
        calls.append((url.split("/")[2], ua))
        if "yahoo" in url:
            if ua is None:
                raise OSError("403")
            return yahoo()
        raise OSError("down")
    c = um.Collector(tmp_path, fetch=fetch, clock=lambda: 1_700_000_100, network=True)
    r = c.refresh()
    assert r["ok"] and r["items"] == 2 and ("query1.finance.yahoo.com", None) in calls
    def dead(url, ua=None):
        raise OSError("blocked")
    c2 = um.Collector(tmp_path, fetch=dead, clock=lambda: 1_700_020_000, network=True)
    r2 = c2.refresh()
    assert not r2["ok"] and r2["items"] == 2 and "QQQ" in r2["error"]                    # 마지막 성공 값은 남기고 오류를 알린다
    p = c2.payload()
    assert p["ok"] and p["items"][0]["age_sec"] > 0 and p["error"]
    def stooq_only(url, ua=None):
        if "stooq" in url:
            return "Symbol,Date,Time,Open,High,Low,Close,Volume\nX,2026-11-27,22:00:00,100,101,99,98,1\n"
        raise OSError("yahoo down")
    c3 = um.Collector(tmp_path / "s", fetch=stooq_only, network=True)
    assert c3.refresh()["ok"] and c3.load()["items"][0]["source"].startswith("stooq")


def test_no_network_during_tests_and_wire_is_idempotent(tmp_path):
    assert um.Collector(tmp_path).network is False
    for rel in ("ena_bot/dashboard.py", "ena_bot/us_market.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, tmp_path / rel)
    wire = ROOT / "wire_us_market.py"
    r = subprocess.run(["python3", str(wire), str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    src = (tmp_path / "ena_bot/dashboard.py").read_text(encoding="utf-8")
    assert "/api/us-market" in src and "us_market.configure(cfg.data_path)" in src
    compile(src, "dashboard.py", "exec")
    assert "이미" in subprocess.run(["python3", str(wire), str(tmp_path)], capture_output=True, text=True).stdout


def test_probe_reports_each_candidate_with_status_and_head():
    def fetch(url, ua=None):
        if "yahoo" in url:
            raise OSError("HTTP 429")
        return '{"a": 1}\n{"b": 2}'
    rows = um.probe(["https://query1.finance.yahoo.com/x", "https://example.com/y"], fetch=fetch)
    assert any("HTTP 429" in st for _, st, _ in rows) and any(st.startswith("성공") and "⏎" in head for _, st, head in rows)


def test_rate_limit_backs_off_longer(tmp_path):
    t = [1000.0]
    c = um.Collector(tmp_path, fetch=lambda u, ua=None: (_ for _ in ()).throw(OSError("HTTP 429")), clock=lambda: t[0], network=True)
    c.poke(); c._thread.join(3)
    assert "429" in c.error
    t[0] += 120
    c._thread = None
    c.poke()
    assert c._thread is None                                         # 2분 뒤에도 쉬는 중
    t[0] += 600
    c.poke()
    assert c._thread is not None
