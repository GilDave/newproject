import importlib.util
import pathlib
import unittest

import numpy as np

P = pathlib.Path(__file__).resolve().parents[1] / "patches/ena-bot/files/ena_pattern_study.py"
spec = importlib.util.spec_from_file_location("ps", P); ps = importlib.util.module_from_spec(spec); spec.loader.exec_module(ps)


def planted_w():
    """하락 → 저점1(30) → 목선(45) → 저점2(60, 같은 높이) → 상승해 목선 돌파."""
    pts = [(0, 100), (30, 90), (45, 100), (60, 90), (80, 108)]
    c = np.interp(np.arange(0, 100), [p[0] for p in pts], [p[1] for p in pts])
    c[81:] = 108.0
    return c


def bars(c):
    return c + 0.3, c - 0.3


class T(unittest.TestCase):
    def test_planted_double_bottom_fires_once_after_the_neckline_break(self):
        c = planted_w(); h, l = bars(c)
        ev = ps.find_events(h, l, c, "W")
        self.assertEqual(len(ev), 1)
        i = ev[0]
        self.assertGreater(c[i], 100.0)                 # 목선(≈100.3) 위에서 종가 돌파
        self.assertTrue(np.all(c[60:i] <= 100.3 + 1e-9))   # 돌파 전에는 목선 아래
        self.assertEqual(ps.find_events(h, l, c, "M"), [])  # 쌍봉은 없다

    def test_mirror_image_is_a_double_top(self):
        c = 200 - planted_w(); h, l = bars(c)
        self.assertEqual(len(ps.find_events(h, l, c, "M")), 1)
        self.assertEqual(ps.find_events(h, l, c, "W"), [])

    def test_failed_pattern_is_invalidated(self):
        c = planted_w(); c[62:] = np.linspace(c[62], 70.0, len(c) - 62)   # 저점 아래로 무너지면 무효
        h, l = bars(c)
        self.assertEqual(ps.find_events(h, l, c, "W"), [])

    def test_no_look_ahead(self):
        rng = np.random.default_rng(3)
        c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 3000)))
        h, l = c * 1.003, c * 0.997
        full = [i for i in ps.find_events(h, l, c, "W") if i < 2000]
        part = ps.find_events(h[:2000], l[:2000], c[:2000], "W")
        self.assertEqual(full, part)
        self.assertGreater(len(full), 0)

    def test_study_flags_a_planted_edge_and_rejects_noise(self):
        def coin(seed, edge):
            rng = np.random.default_rng(seed)
            n = 12000
            c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
            if edge:   # W 돌파 직후 24봉 동안 +4% 를 심는다(같은 잡음 경로 위에)
                h, l = c * 1.003, c * 0.997
                r = np.zeros(n)
                for i in ps.find_events(h, l, c, "W"):
                    r[i + 1:i + 25] += 0.04 / 24
                c = c * np.exp(np.cumsum(r))
            ts = (1_685_577_600_000 // 3_600_000 + np.arange(n)) * 3_600_000   # 2023-06 부터: 2024 경계를 걸친다
            return np.c_[ts, c, c * 1.003, c * 0.997, c, np.ones(n)]
        noise = ps.study({f"N{k}": coin(k, False) for k in range(4)}, kinds=("W",))["W"]
        self.assertFalse(noise["pass"])
        edge = ps.study({f"E{k}": coin(k, True) for k in range(4)}, kinds=("W",))["W"]
        self.assertTrue(edge["pass"], edge["verdict"])
        self.assertIn("합격", edge["verdict"])


if __name__ == "__main__":
    unittest.main()
