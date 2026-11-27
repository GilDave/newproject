"""ENA 저장소에서 실행(ena_econ_study.py 가 프로젝트 루트에 있어야 함)."""
import numpy as np

import ena_econ_study as s

H = 3_600_000


def test_event_hour_ratio_detects_big_move_and_skips_thin_baseline():
    t0 = 1_700_000_000_000 // H * H
    rows = []
    for k in range(24 * 60):
        ts = t0 + k * H
        rows.append([ts, 100.0, 100.1, 99.9, 100.05, 1.0])            # 평소 폭 0.2%
    a = np.array(rows)
    ev = int(a[24 * 30, 0]) + 30 * 60_000                              # 30일째 어느 시각
    i = int(np.searchsorted(a[:, 0], s.to_ms(s.bar_of(ev))))
    a[i, 2], a[i, 3], a[i, 4] = 102.0, 98.0, 101.0                     # 그 봉만 폭 4%
    res = s.event_ratios(a, [ev])
    assert len(res) == 1 and res[0][1] > 10
    assert s.event_ratios(a[:20], [ev]) == []                          # 데이터가 모자라면 건너뜀
