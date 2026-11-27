import importlib.util
import pathlib
import sys
import unittest

import numpy as np

D = pathlib.Path(__file__).resolve().parents[1] / "patches/ena-bot/files"
sys.path.insert(0, str(D))
spec = importlib.util.spec_from_file_location("sw", D / "ena_pattern_sweep.py"); sw = importlib.util.module_from_spec(spec); spec.loader.exec_module(sw)

PRE_T = 1_600_000_000_000          # 2020-09 (2024-01 이전)
POST_T = 1_720_000_000_000         # 2024-07 (이후)


def rows(pre_mean, post_mean, n_pre=150, n_post=150, seed=0, sd=0.03):
    rng = np.random.default_rng(seed)
    t_pre = PRE_T + np.arange(n_pre) * 86_400_000 * 3
    t_post = POST_T + np.arange(n_post) * 86_400_000 * 2
    ex_pre = rng.normal(pre_mean + sw.p1.COST_RT, sd, n_pre)        # 비용 전 총 초과 수익
    ex_post = rng.normal(post_mean + sw.p1.COST_RT, sd, n_post)
    return np.c_[np.r_[t_pre, t_post], np.r_[ex_pre, ex_post]]


class T(unittest.TestCase):
    def test_selection_uses_only_the_pre_period(self):
        a = rows(+0.02, -0.01, seed=1)          # 이전엔 최고, 이후엔 나쁨 → 선택되지만 합격 못 함(과최적화)
        b = rows(+0.00, +0.02, seed=2)
        r = sw.evaluate({(3, 1.0): a, (4, 1.0): b}, iters=300)
        self.assertEqual(r["chosen"], {"k": 3, "s": 1.0})
        self.assertFalse(r["pass"])
        # 이후 구간 값을 바꿔도 선택은 바뀌지 않는다
        a2 = a.copy(); a2[a2[:, 0] >= sw.p1.SPLIT_MS, 1] += 0.5
        self.assertEqual(sw.evaluate({(3, 1.0): a2, (4, 1.0): b}, iters=300)["chosen"], {"k": 3, "s": 1.0})

    def test_persistent_edge_passes_and_noise_does_not(self):
        good = sw.evaluate({(3, 1.0): rows(0.03, 0.03, 300, 300, 3), (4, 1.0): rows(0.02, 0.02, 300, 300, 4)}, iters=500)
        self.assertTrue(good["pass"], good)
        noise = sw.evaluate({(3, 1.0): rows(0.0, 0.0, 300, 300, 5), (4, 1.0): rows(0.0, 0.0, 300, 300, 6)}, iters=500)
        self.assertFalse(noise["pass"])

    def test_too_few_events(self):
        self.assertEqual(sw.evaluate({(3, 1.0): rows(0.03, 0.03, 50, 300, 7)}, iters=100)["verdict"], "이전 구간 이벤트 부족")
        self.assertEqual(sw.evaluate({(3, 1.0): rows(0.03, 0.03, 300, 50, 8)}, iters=100)["verdict"], "이후 구간 이벤트 부족")

    def test_grid_sizes(self):
        self.assertEqual(len(sw.combos("P1")), 9)
        self.assertEqual(len(sw.combos("P9")), 3)       # 박스권은 피벗이 없어 k 가 없다
        self.assertEqual(sw.TESTS, 24)


if __name__ == "__main__":
    unittest.main()
