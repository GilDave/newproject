"""ENA 저장소에서 실행하는 테스트: ena_pattern_learn.py · ena_pattern_study.py · ena_pattern_study2.py 를 ENA 루트에 두고 tests/ 로 복사해 쓴다."""
import numpy as np

import ena_pattern_learn as pl


def bars_from(c):
    n = len(c)
    ts = (1_700_000_000_000 // 14_400_000 + np.arange(n)) * 14_400_000
    return np.c_[ts, c, c + 0.3, c - 0.3, c, np.ones(n)]


def w_path(n=260):
    c = np.interp(np.arange(0, 240), [0, 150, 165, 180, 200, 230, 239], [100, 90, 100, 90, 95, 108, 108])
    return np.r_[c, np.full(n - 240, 108.0)]


def test_window_max_only_looks_backwards():
    a = np.array([0, 1, 0, 0, 0, 0, 0, 0.0])
    assert list(pl.window_max(a, 3)) == [0, 1, 1, 1, 0, 0, 0, 0]          # 이벤트 발생 봉 포함 과거 3봉
    assert list(pl.window_max(np.zeros(4), 6)) == [0, 0, 0, 0]


def test_planted_pattern_lights_the_right_column_for_six_bars_and_the_sign_follows_trend():
    b = bars_from(w_path())
    n = len(b)
    up = pl.pattern_features(b, np.ones(n))
    k = pl.FEAT_NAMES.index("pat_P0W")
    on = np.where(up[:, k] != 0)[0]
    assert len(on) == 6 and (np.diff(on) == 1).all()                      # 최근 6봉(24시간) 동안만 켜진다
    assert (up[on, k] == 1.0).all()                                        # 롱형 패턴 × 상승 추세 = +1
    assert (up[on, pl.FEAT_NAMES.index("pat_net6")] > 0).all()
    down = pl.pattern_features(b, -np.ones(n))
    assert (down[on, k] == -1.0).all()                                     # 추세 방향이 반대면 부호가 뒤집힌다
    none = pl.pattern_features(b, np.zeros(n))
    assert not none.any()                                                  # 추세 방향이 없는 행은 0


def test_features_never_use_future_bars():
    rng = np.random.default_rng(4)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 2500)))
    b, d = bars_from(c), np.ones(len(c))
    full = pl.pattern_features(b, d)
    m = 1800
    part = pl.pattern_features(b[:m], d[:m])
    assert np.array_equal(full[:m - 1], part[:m - 1])                       # 뒤쪽을 잘라도 앞쪽 특징이 같다
    assert (np.abs(full[:, :12]).sum(1) > 0).any()                          # 무작위 데이터에서도 패턴 이벤트는 실제로 나온다


def test_feature_shape_and_names_line_up():
    assert len(pl.FEAT_NAMES) == 14 and pl.PATS[0] == "P0W" and len(pl.PATS) == 12
    assert pl.pattern_features(bars_from(w_path()), np.ones(260)).shape == (260, 14)
