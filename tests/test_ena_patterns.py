import importlib.util
import pathlib
import sys
import unittest

import numpy as np

D = pathlib.Path(__file__).resolve().parents[1] / "patches/ena-bot/files"
sys.path.insert(0, str(D))


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, D / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


pt = _load("patterns_mod", "patterns.py")
ps2 = _load("ps2_for_patterns", "ena_pattern_study2.py")
sys.path.pop(0)


def hl(c):
    return c + 0.3, c - 0.3


def pw(points, n):
    return np.interp(np.arange(n), [p[0] for p in points], [p[1] for p in points])


def w_shape():
    c = pw([(0, 100), (30, 90), (45, 100), (60, 90), (80, 108)], 100)
    c[81:] = 108.0
    return c


class Equivalence(unittest.TestCase):
    def test_overlay_detector_finds_exactly_the_events_that_were_statistically_tested(self):
        rng = np.random.default_rng(11)
        for seed in range(4):
            c = 100 * np.exp(np.cumsum(np.random.default_rng(seed).normal(0, 0.01, 3500)))
            h, l = c * (1 + 0.003 * rng.random(len(c))), c * (1 - 0.003 * rng.random(len(c)))
            for pat in pt.NAMES:
                self.assertEqual(pt.event_indices(pat, h, l, c), ps2.events_for(pat, h, l, c), f"{pat} seed={seed}")

    def test_equivalence_holds_for_other_parameters_too(self):
        c = 100 * np.exp(np.cumsum(np.random.default_rng(5).normal(0, 0.01, 3000)))
        h, l = c * 1.003, c * 0.997
        for pat in pt.NAMES:
            for k, s in ((3, 0.7), (6, 1.5)):
                self.assertEqual(pt.event_indices(pat, h, l, c, k, s), ps2.events_for(pat, h, l, c, k, s), f"{pat} k={k} s={s}")

    def test_equivalence_on_planted_shapes(self):
        shapes = [("P0W", w_shape()), ("P0M", 200 - w_shape()),
                  ("P1", pw([(0, 100), (30, 90), (45, 100), (60, 80), (75, 100), (90, 90), (110, 108), (130, 108)], 131))]
        for pat, c in shapes:
            h, l = hl(c)
            ev = pt.event_indices(pat, h, l, c)
            self.assertEqual(ev, ps2.events_for(pat, h, l, c))
            self.assertEqual(len(ev), 1, pat)


class Geometry(unittest.TestCase):
    def test_w_confirmed_has_three_pivots_and_a_neckline(self):
        c = w_shape()
        h, l = hl(c)
        ev, fm = pt.detect("P0W", h, l, c)
        self.assertEqual(len(ev), 1)
        g = ev[0]
        self.assertEqual(g["state"], "confirmed")
        (i1, v1), (iq, vq), (i2, v2) = g["points"]
        self.assertTrue(i1 < iq < i2 < g["idx"])
        self.assertAlmostEqual(v1, v2, delta=0.5)                 # 두 저점의 높이가 비슷하다
        self.assertAlmostEqual(g["level"], vq, places=6)           # 목선 = 사이 고점
        self.assertGreater(c[g["idx"]], g["level"])                # 돌파 봉 종가가 목선 위
        self.assertEqual(fm, [])                                    # 돌파가 끝났으니 형성 중인 것은 없다

    def test_w_is_forming_before_the_neckline_break_and_never_uses_future_bars(self):
        c = w_shape()
        h, l = hl(c)
        cut = 72                                                    # 두 번째 저점(60)은 확정됐지만 돌파(≈78) 전
        ev, fm = pt.detect("P0W", h[:cut], l[:cut], c[:cut])
        self.assertEqual(ev, [])
        self.assertEqual(len(fm), 1)
        self.assertEqual(fm[0]["state"], "forming")
        self.assertIsNone(fm[0]["idx"])
        self.assertAlmostEqual(fm[0]["level"], fm[0]["points"][1][1], places=6)
        self.assertEqual(fm[0]["lines"][0][1][0], cut - 1)          # 목선은 마지막 봉까지만 그린다

    def test_bearish_pattern_prices_are_flipped_back(self):
        c = 200 - w_shape()
        h, l = hl(c)
        ev, _ = pt.detect("P0M", h, l, c)
        self.assertEqual(len(ev), 1)
        g = ev[0]
        self.assertTrue(all(v > 100 for _, v in g["points"][::2]))   # 쌍봉의 꼭대기는 원래 가격(>100)으로 돌아온다
        self.assertLess(c[g["idx"]], g["level"])

    def test_scan_converts_indices_to_times_and_filters_by_recency(self):
        c = w_shape()
        h, l = hl(c)
        ts = (1_700_000_000_000 // 3_600_000 + np.arange(len(c))) * 3_600_000
        out = pt.scan(ts, h, l, c, recent=100)
        w = [g for g in out if g["pat"] == "P0W"]
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0]["time"], int(ts[w[0]["idx"]]))
        self.assertTrue(all(p[0] in set(int(t) for t in ts) for p in w[0]["points"]))
        self.assertEqual(w[0]["name"], "쌍바닥(W)")
        self.assertEqual(pt.scan(ts, h, l, c, recent=1, pats=["P0W"]), [])    # 오래된 이벤트는 recent 밖


class ChartSafety(unittest.TestCase):
    """차트 라이브러리는 시간이 엄격히 오름차순이 아니면 오류를 내 차트 전체가 깨진다(실제로 4시간 차트가 비는 문제가 있었다)."""

    def test_geometry_points_and_lines_are_always_strictly_time_ordered(self):
        t = np.arange(160.0)
        tri = 100 + (10 - 0.1 * t) * np.sin(2 * np.pi * t / 20)
        tri[78:] += np.minimum((t[78:] - 78) * 0.8, 12)
        series = [tri]
        for seed in range(6):
            series.append(100 * np.exp(np.cumsum(np.random.default_rng(seed).normal(0, 0.01, 3000))))
        checked = 0
        for c in series:
            h, l = c * 1.003, c * 0.997
            for pat in pt.NAMES:
                ev, fm = pt.detect(pat, h, l, c)
                for g in ev + fm:
                    xs = [p[0] for p in g["points"]]
                    self.assertEqual(xs, sorted(set(xs)), f"{pat} 점이 시간순이 아닙니다")
                    for (a, _), (b, _) in g["lines"]:
                        self.assertLess(a, b, f"{pat} 선의 시작이 끝보다 늦습니다")
                    checked += 1
        self.assertGreater(checked, 50)                           # 실제로 도형이 많이 만들어졌다

    def test_triangle_geometry_specifically_is_ordered(self):
        t = np.arange(120.0)
        c = 100 + (10 - 0.1 * t) * np.sin(2 * np.pi * t / 20)
        c[78:] += np.minimum((t[78:] - 78) * 0.8, 12)
        ev, _ = pt.detect("P3", c + 0.3, c - 0.3, c)
        self.assertGreaterEqual(len(ev), 1)
        xs = [p[0] for p in ev[0]["points"]]
        self.assertEqual(xs, sorted(xs))


class Explanations(unittest.TestCase):
    def test_every_pattern_has_a_complete_plain_language_explanation(self):
        for k in pt.NAMES:
            e = pt.EXPLAIN[k]
            self.assertEqual(set(e), {"bias", "shape", "reads", "confirm", "fail", "caution"}, k)
            self.assertTrue(all(len(v) > 10 for v in e.values()), k)

    def test_stated_bias_matches_the_detector_direction_and_is_never_a_recommendation(self):
        for k, e in pt.EXPLAIN.items():
            self.assertTrue(e["bias"].startswith("롱형" if pt.DIR[k] > 0 else "숏형"), k)
            for banned in ("사세요", "파세요", "매수하", "매도하", "추천합니다", "수익이 납니다"):
                self.assertNotIn(banned, " ".join(e.values()), k)
        self.assertIn("추천이 아닙니다", pt.GLOSSARY)
        self.assertIn("롱 =", pt.GLOSSARY)
        self.assertIn("숏 =", pt.GLOSSARY)


if __name__ == "__main__":
    unittest.main()
