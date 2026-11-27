import importlib.util
import pathlib
import sys
import unittest

import numpy as np

D = pathlib.Path(__file__).resolve().parents[1] / "patches/ena-bot/files"
sys.path.insert(0, str(D))
spec = importlib.util.spec_from_file_location("ps2", D / "ena_pattern_study2.py"); ps2 = importlib.util.module_from_spec(spec); spec.loader.exec_module(ps2)


def hl(c):
    return c + 0.3, c - 0.3


def pw(points, n):
    return np.interp(np.arange(n), [p[0] for p in points], [p[1] for p in points])


def inv_hs():
    c = pw([(0, 100), (30, 90), (45, 100), (60, 80), (75, 100), (90, 90), (110, 108), (130, 108)], 131)
    return c


def triangle():
    t = np.arange(120.0)
    amp = 10 - 0.1 * t
    c = 100 + amp * np.sin(2 * np.pi * t / 20)
    c[78:] += np.minimum((t[78:] - 78) * 0.8, 12)      # 78 번째 봉부터 위로 밀어 올려 저항선을 넘긴다
    return c


def wedge():
    t = np.arange(130.0)
    mid, half = 100 - 0.3 * t, 10 - 0.1 * t
    c = mid + half * np.sin(2 * np.pi * t / 20)
    c[80:] += np.minimum((t[80:] - 80) * 0.9, 14)
    return c


def flag():
    return pw([(0, 100), (20, 100), (30, 130), (50, 120), (62, 135), (90, 135)], 91)


def box():
    t = np.arange(130.0)
    c = 100 + np.sin(t)
    c[100:] = 100 + np.minimum((t[100:] - 99) * 3.0, 9)
    return c


class T(unittest.TestCase):
    def ev(self, pat, c):
        h, l = hl(c)
        return ps2.events_for(pat, h, l, c)

    def test_inverse_head_and_shoulders_and_mirror(self):
        c = inv_hs()
        e = self.ev("P1", c)
        self.assertEqual(len(e), 1)
        self.assertTrue(95 <= e[0] <= 105, e)             # 목선(100)을 넘는 구간
        self.assertEqual(self.ev("P2", c), [])
        m = 200 - c
        self.assertEqual(len(self.ev("P2", m)), 1)
        self.assertEqual(self.ev("P1", m), [])

    def test_triangle_breakout_up_and_mirror(self):
        c = triangle()
        e = self.ev("P3", c)
        self.assertGreaterEqual(len(e), 1, "삼각 수렴 상향 돌파를 찾지 못했습니다")
        self.assertTrue(all(i >= 78 for i in e), e)        # 돌파 전에는 신호가 없다
        self.assertGreaterEqual(len(self.ev("P4", 200 - c)), 1)

    def test_falling_wedge_breakout_up_and_mirror(self):
        c = wedge()
        self.assertGreaterEqual(len(self.ev("P5", c)), 1, "하락 쐐기 상향 돌파를 찾지 못했습니다")
        self.assertGreaterEqual(len(self.ev("P6", 200 - c)), 1)
        self.assertEqual(self.ev("P3", c), [])             # 쐐기는 삼각(저항 ≤ 0 ≤ 지지)과 구분된다

    def test_bull_flag_and_mirror(self):
        c = flag()
        e = self.ev("P7", c)
        self.assertEqual(len(e), 1)
        self.assertTrue(55 <= e[0] <= 65, e)
        self.assertEqual(len(self.ev("P8", 300 - c)), 1)

    def test_range_breakout_and_mirror(self):
        c = box()
        e = self.ev("P9", c)
        self.assertEqual(len(e), 1)
        self.assertTrue(98 <= e[0] <= 102, e)
        self.assertEqual(len(self.ev("P10", 200 - c)), 1)

    def test_no_look_ahead_for_every_pattern(self):
        rng = np.random.default_rng(7)
        c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 4000)))
        h, l = c * 1.003, c * 0.997
        for pat in ps2.NAMES:
            full = [i for i in ps2.events_for(pat, h, l, c) if i < 3000]
            part = ps2.events_for(pat, h[:3000], l[:3000], c[:3000])
            self.assertEqual(full, part, pat)

    def test_resample4_keeps_only_complete_utc_aligned_groups(self):
        ts = (1_700_000_000_000 // 3_600_000 // 4 * 4 + np.arange(10)) * 3_600_000
        a = np.c_[ts, np.arange(10.0), np.arange(10.0) + 1, np.arange(10.0) - 1, np.arange(10.0) + 0.5, np.ones(10)]
        r = ps2.resample4(a)
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0, 2], 4.0)                       # 4개 봉의 최고가
        self.assertEqual(r[0, 3], -1.0)

    def test_study_rejects_noise_and_accepts_a_planted_edge(self):
        def coin(seed, edge):
            rng = np.random.default_rng(seed)
            n = 20000
            c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
            if edge:
                h, l = c * 1.003, c * 0.997
                r = np.zeros(n)
                for i in ps2.events_for("P9", h, l, c):
                    r[i + 1:i + 25] += 0.04 / 24
                c = c * np.exp(np.cumsum(r))
            ts = (1_685_577_600_000 // 3_600_000 + np.arange(n)) * 3_600_000
            return np.c_[ts, c, c * 1.003, c * 0.997, c, np.ones(n)]
        noise = ps2.study_tf({f"N{k}": coin(k, False) for k in range(6)}, "1h", pats=["P9"], iters=300)["P9"]
        self.assertFalse(noise["pass"], noise["verdict"])
        edge = ps2.study_tf({f"E{k}": coin(k, True) for k in range(6)}, "1h", pats=["P9"], iters=300)["P9"]
        self.assertTrue(edge["pass"], (edge["verdict"], edge["events"]))


if __name__ == "__main__":
    unittest.main()
