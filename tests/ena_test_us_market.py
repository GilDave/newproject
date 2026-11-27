"""ENA 저장소에서 실행(us_market.py 를 ena_bot/ 에 두고 wire_us_market.py 적용 후 tests/ 로 복사)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ena_bot import us_market as um

ROOT = Path(__file__).resolve().parents[1]


CNBC = json.dumps({"FormattedQuoteResult": {"FormattedQuote": [
    {"symbol": "QQQ", "last": "645.26", "last_time": "2026-11-27T11:06:10.047-0500", "change": "+10.87", "change_pct": "+1.71%"},
    {"symbol": ".IXIC", "last": "23,100.50", "last_time": "2026-11-27T11:06:10.047-0500", "change": "-100.5", "change_pct": "-0.43%"}]}})
NASDAQ = json.dumps({"data": {"symbol": "QQQ", "primaryData": {"lastSalePrice": "$644.80", "netChange": "+10.87", "percentageChange": "+1.71%", "deltaIndicator": "up"}}})


def test_parse_cnbc_and_nasdaq_pick_the_right_symbol_and_values():
    q = um.parse_cnbc(CNBC, "QQQ")
    assert abs(q["last"] - 645.26) < 1e-9 and abs(q["pct_day"] - 1.71) < 1e-9 and q["source"] == "cnbc" and q["ts"] > 1_700_000_000_000
    i = um.parse_cnbc(CNBC, ".IXIC")
    assert abs(i["last"] - 23100.5) < 1e-9 and i["pct_day"] < 0
    n = um.parse_nasdaq(NASDAQ, "QQQ")
    assert abs(n["last"] - 644.8) < 1e-9 and abs(n["pct_day"] - 1.71) < 1e-9
    down = um.parse_nasdaq(NASDAQ.replace("+1.71%", "1.71%").replace('"up"', '"down"'), "QQQ")
    assert down["pct_day"] < 0                                              # 부호 없이 내려옴 표기
    for bad in ("{}", json.dumps({"FormattedQuoteResult": {"FormattedQuote": []}}), "not json"):
        with pytest.raises(Exception):
            um.parse_cnbc(bad, "QQQ")
    with pytest.raises(ValueError):
        um.parse_nasdaq(json.dumps({"data": {"primaryData": {}}}), "QQQ")
    no_pct = json.dumps({"FormattedQuoteResult": {"FormattedQuote": [{"symbol": "QQQ", "last": "110", "change": "10"}]}})
    assert abs(um.parse_cnbc(no_pct, "QQQ")["pct_day"] - 10.0) < 1e-9       # 등락률이 없으면 변화량으로 계산


def test_chain_tries_safari_first_falls_back_to_nasdaq_and_keeps_old_values(tmp_path):
    calls = []
    def fetch(url, ua=None):
        calls.append((url.split("/")[2], bool(ua)))
        if "cnbc" in url:
            if ua is None:
                raise OSError("HTTP 403")
            return CNBC
        return NASDAQ
    c = um.Collector(tmp_path, fetch=fetch, clock=lambda: 1_800_000_000, network=True)
    r = c.refresh()
    assert r["ok"] and r["items"] == 2 and calls[0] == ("quote.cnbc.com", True)
    def cnbc_down(url, ua=None):
        if "cnbc" in url:
            raise OSError("HTTP 429")
        return NASDAQ
    c2 = um.Collector(tmp_path, fetch=cnbc_down, clock=lambda: 1_800_000_900, network=True)
    r2 = c2.refresh()
    assert not r2["ok"] and r2["items"] == 2 and ".IXIC" in r2["error"]            # QQQ 는 Nasdaq.com 으로, 지수는 이전 값 유지
    assert c2.load()["items"][0]["source"] in ("nasdaq.com", "cnbc")
    p = c2.payload()
    assert p["ok"] and p["error"] and all(x["age_sec"] >= 0 for x in p["items"])


def test_one_hour_change_is_computed_from_our_own_samples(tmp_path):
    t = [1_800_000_000.0]
    price = [100.0]
    def fetch(url, ua=None):
        return json.dumps({"FormattedQuoteResult": {"FormattedQuote": [{"symbol": s, "last": str(price[0]), "change_pct": "0.5%", "last_time": "2026-11-27T11:06:10.047-0500"} for s in ("QQQ", ".IXIC")]}})
    c = um.Collector(tmp_path, fetch=fetch, clock=lambda: t[0], network=True)
    c.refresh()
    assert c.load()["items"][0]["pct_1h"] is None                                    # 아직 1시간 전 기록이 없다
    t[0] += 3600; price[0] = 99.0
    c.refresh()
    assert abs(c.load()["items"][0]["pct_1h"] + 1.0) < 1e-6


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
