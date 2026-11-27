"""ENA 저장소에서 실행하는 테스트: ena_usde_forward.py 와 같은 폴더의 ena_altdata_lab.py · ena_idea_lab.py · ena_macro_study.py 가 ENA 루트에 있어야 한다."""
import json
from pathlib import Path

import numpy as np

import ena_usde_forward as fw

DAY = 86_400_000
D0 = 1_700_000_000_000 // DAY * DAY


def series(n=80, drop_from=60):
    d = D0 + np.arange(n, dtype=np.int64) * DAY
    v = np.where(np.arange(n) < drop_from, 100.0, 100.0 - (np.arange(n) - drop_from) * 1.0)
    return d, v


def test_build_rows_flags_a_30d_decline():
    rows = fw.build_rows(series(), series())
    assert rows[0]["usde_flag"] == 0 and rows[-1]["usde_flag"] == 1
    assert all(r["date"] for r in rows) and len(rows) == 80 - 30


def test_first_log_starts_today_and_never_backfills(tmp_path):
    p = tmp_path / "log.csv"
    rows = fw.build_rows(series(), series())
    last = rows[-1]["date"]
    n = fw.append_new(rows, p, today=last)
    assert n == 1 and len(fw.read_log(p)) == 1                  # 첫 기록은 오늘 한 건만(과거 소급 금지)
    later = fw.build_rows(*[(np.r_[s[0], s[0][-1] + DAY * np.arange(1, 4)], np.r_[s[1], [30.0, 29.0, 28.0]]) for s in (series(), series())])
    assert fw.append_new(later, p, today=later[-1]["date"]) == 3  # 이후에는 새 날짜만 이어 붙인다
    assert fw.append_new(later, p, today=later[-1]["date"]) == 0  # 다시 실행해도 중복 없음


def test_cmd_log_reports_failure_when_a_source_is_missing(tmp_path):
    def fetch(url):
        raise OSError("blocked")
    assert fw.cmd_log(str(tmp_path / "c"), fetch=fetch, path=tmp_path / "l.csv") == 1


def test_evaluate_needs_enough_records(tmp_path):
    assert fw.evaluate(tmp_path / "none.csv")["ok"] is False


def _fake_fetch_factory(n=80):
    import datetime as dt
    today = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    secs = [int((today - dt.timedelta(days=n - 1 - i)).timestamp()) for i in range(n)]
    vals = [100.0 if i < n - 25 else 100.0 - (i - (n - 25)) for i in range(n)]
    chart = json.dumps([{"date": str(t), "totalCirculatingUSD": {"peggedUSD": v}} for t, v in zip(secs, vals)])
    tvl = json.dumps({"tvl": [{"date": t, "totalLiquidityUSD": v} for t, v in zip(secs, vals)]})

    def fetch(url):
        if "alternative.me" in url:
            return json.dumps({"data": [{"value": "50", "timestamp": str(secs[0])}]})
        if "stablecoins?includePrices" in url:
            return json.dumps({"peggedAssets": [{"id": "146", "symbol": "USDe"}]})
        if "stablecoincharts" in url:
            return chart
        if "api.llama.fi/protocol" in url:
            return tvl
        raise OSError(url)
    return fetch


def test_daily_logs_refreshes_and_writes_the_eval_file(tmp_path):
    out = tmp_path / "eval.txt"
    stub = {"ok": True, "start": "2026-11-28", "last_bar": "2026-11-28", "days": 10, "stage": "표본 부족", "variants": {}}
    rc = fw.cmd_daily(path=tmp_path / "log.csv", out=out, fetch=_fake_fetch_factory(), refresh=lambda: (False, "offline"),
                      evaluate_fn=lambda p: stub, alt_cache=str(tmp_path / "c"))
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    assert "갱신]" in text and "표본 부족" in text and "ENA 시세 갱신 실패(offline)" in text      # 갱신 실패는 숨기지 않는다
    assert len(fw.read_log(tmp_path / "log.csv")) == 1                                         # 첫 기록은 오늘 한 건
    # 두 번째 실행은 기록을 늘리지 않고 평가 파일만 다시 쓴다
    assert fw.cmd_daily(path=tmp_path / "log.csv", out=out, fetch=_fake_fetch_factory(), refresh=lambda: (True, ""),
                        evaluate_fn=lambda p: stub, alt_cache=str(tmp_path / "c")) == 0
    assert len(fw.read_log(tmp_path / "log.csv")) == 1 and "시세 갱신 실패" not in out.read_text(encoding="utf-8")


def test_daily_does_not_evaluate_when_logging_fails(tmp_path):
    def broken(url):
        raise OSError("blocked")
    out = tmp_path / "eval.txt"
    assert fw.cmd_daily(path=tmp_path / "log.csv", out=out, fetch=broken, refresh=lambda: (True, ""), evaluate_fn=lambda p: {"ok": True},
                        alt_cache=str(tmp_path / "c")) == 1
    assert not out.exists()


def test_refresh_ena_detects_a_silently_failed_update(tmp_path, monkeypatch):
    import time
    import ena_bot.learn_data as ld
    monkeypatch.setattr(ld.time, "sleep", lambda s: None)               # 재시도 대기 시간을 없앤다
    class Boom:
        def _request(self, *a, **k):
            raise RuntimeError("network down")
    ok, why = fw.refresh_ena(str(tmp_path), client=Boom())
    assert ok is False and why                                           # 요청이 전부 실패 → 캐시가 없거나 낡음을 알린다
    now_ms = int(time.time() * 1000) // 3_600_000 * 3_600_000
    np.save(tmp_path / "ENAUSDT_k60.npy", np.array([[now_ms - 3_600_000, 1, 1, 1, 1, 1.0]]))
    monkeypatch.setattr(ld, "update_symbol", lambda *a, **k: {})        # 갱신은 아무것도 못 했지만 캐시는 최근 것
    assert fw.refresh_ena(str(tmp_path), client=Boom()) == (True, "")
    np.save(tmp_path / "ENAUSDT_k60.npy", np.array([[now_ms - 30 * 3_600_000, 1, 1, 1, 1, 1.0]]))
    ok, why = fw.refresh_ena(str(tmp_path), client=Boom())
    assert ok is False and "시간 전" in why
