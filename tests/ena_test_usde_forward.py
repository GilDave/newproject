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
