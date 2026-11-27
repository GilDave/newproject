import pathlib
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

D = pathlib.Path(__file__).resolve().parents[1] / "patches/ena-bot/files"
sys.path.insert(0, str(D))
import ena_open_study as s  # noqa: E402
sys.path.pop(0)


def fake(days=60, boost=3.0, seed=0):
    rng = np.random.default_rng(seed)
    t = datetime(2025, 1, 6, 5, tzinfo=timezone.utc)                 # 겨울: ET = UTC-5, 05:00 UTC = 00:00 ET
    rows = []
    for k in range(days * 24):
        ts = t + timedelta(hours=k)
        et = ts - timedelta(hours=5)
        wd = et.weekday() < 5
        r = 0.004 * (boost if (wd and et.hour == 9) else 1.0) * rng.uniform(0.8, 1.2)
        rows.append([ts.timestamp() * 1000, 1.0, 1.0 + r, 1.0, 1.0, 100.0 * (boost if (wd and et.hour == 9) else 1.0)])
    return np.array(rows)


def test_et_fields_follow_new_york_wall_clock_across_dst():
    ts = np.array([datetime(2025, 1, 15, 14, tzinfo=timezone.utc).timestamp() * 1000, datetime(2025, 7, 15, 13, tzinfo=timezone.utc).timestamp() * 1000])
    _, hour, wd = s.et_fields(ts)
    assert list(hour) == [9, 9] and list(wd) == [2, 1]                # 겨울 14시 UTC, 여름 13시 UTC 모두 ET 9시


def test_weekday_only_boost_shows_up_in_that_hour_and_not_elsewhere():
    res = s.study({"A": fake(), "B": fake(seed=1)})
    r9, r6 = res[9]["range"], res[6]["range"]
    assert r9[2] > 2.0 and r9[3] > 1.5 and 0.85 < r6[2] < 1.15
    assert s.study({"A": fake(days=10)}) == {}                       # 표본이 모자라면 계산하지 않는다
    assert "개장" in s.render(res, res)
