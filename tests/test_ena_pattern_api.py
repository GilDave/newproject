import json
import pathlib
import sys
import tempfile
import unittest

import numpy as np

D = pathlib.Path(__file__).resolve().parents[1] / "patches/ena-bot/files"
sys.path.insert(0, str(D))
import pattern_api as pa  # noqa: E402
sys.path.pop(0)

H1 = 3_600_000


def w_rows(n_tail=20, open_candle=True, now=1_800_000_000_000 // H1 * H1):
    c = np.interp(np.arange(0, 240), [0, 150, 165, 180, 200, 230, 239], [100, 90, 100, 90, 95, 108, 108])     # 쌍바닥 + 목선 돌파(약 207번째 봉)
    c = np.r_[c, np.full(n_tail, 108.0)]
    rows = [[now - (len(c) - i) * H1, c[i] - 0.1, c[i] + 0.3, c[i] - 0.3, c[i], 1.0] for i in range(len(c))]
    if open_candle:
        rows.append([now, 108.0, 109.0, 107.0, 108.5, 1.0])      # 아직 닫히지 않은 마지막 봉
    return rows, now


class T(unittest.TestCase):
    def test_unsupported_interval_explains_why_and_returns_nothing(self):
        rows, now = w_rows()
        out = pa.payload(lambda iv, n: rows, "ENAUSDT", "D", now)
        self.assertFalse(out["supported"])
        self.assertIn("1시간·4시간", out["reason"])
        self.assertEqual(out["patterns"], [])
        self.assertIn("신호로 쓰지", out["disclaimer"])
        self.assertIn("롱 =", out["glossary"])                        # 지원하지 않는 시간봉에서도 용어 설명은 볼 수 있다

    def test_open_candle_is_never_used(self):
        rows, now = w_rows(open_candle=True)
        out = pa.payload(lambda iv, n: rows, "ENAUSDT", "60", now + 10_000)
        self.assertEqual(out["bars"], len(rows) - 1)
        self.assertEqual(out["last_ts"], rows[-2][0])

    def test_returns_detected_patterns_with_stats_and_metadata(self):
        rows, now = w_rows()
        with tempfile.TemporaryDirectory() as d:
            sp = pathlib.Path(d) / "stats.json"
            sp.write_text(json.dumps({"generated": "2026-11-27", "coins": 18, "cost_round_trip_pct": 0.15,
                                      "tf": {"1h": {"horizon": "24시간 뒤", "patterns": {"P0W": {"events": 3902, "verdict": "근거 없음"}}}}}), encoding="utf-8")
            out = pa.payload(lambda iv, n: rows, "ENAUSDT", "60", now + 10_000, stats_path=sp)
        self.assertTrue(out["supported"])
        self.assertEqual(out["tf"], "1h")
        self.assertEqual(out["horizon"], "24시간 뒤")
        self.assertEqual(out["stats"]["P0W"]["events"], 3902)
        self.assertEqual(out["stats_meta"]["coins"], 18)
        w = [g for g in out["patterns"] if g["pat"] == "P0W"]
        self.assertEqual(len(w), 1)                                   # 심어 둔 쌍바닥을 실제로 찾는다
        self.assertEqual(w[0]["state"], "confirmed")
        self.assertTrue(all({"pat", "name", "state", "points", "lines", "dir", "bars_ago"} <= set(g) for g in out["patterns"]))
        self.assertIn("P0W", out["explain"])                          # 감지된 패턴마다 초보자용 설명이 함께 간다
        self.assertEqual(set(out["explain"]), {g["pat"] for g in out["patterns"]})
        self.assertIn("추천이 아닙니다", out["glossary"])
        json.dumps(out)                                              # 응답은 그대로 JSON 으로 직렬화돼야 한다(넘파이 값이 새지 않는다)

    def test_missing_stats_file_still_works(self):
        rows, now = w_rows()
        out = pa.payload(lambda iv, n: rows, "ENAUSDT", "60", now + 10_000, stats_path=pathlib.Path("/nonexistent/x.json"))
        self.assertTrue(out["supported"])
        self.assertEqual(out["stats"], {})
        self.assertIsNone(out["stats_meta"])

    def test_too_few_bars(self):
        rows, now = w_rows()
        out = pa.payload(lambda iv, n: rows[-50:], "ENAUSDT", "240", now + 10_000 * 4_000)
        self.assertEqual(out["patterns"], [])
        self.assertIn("부족", out["reason"])

    def test_shipped_stats_table_is_complete_and_marks_every_pattern_without_evidence(self):
        st = json.loads((D / "pattern_stats.json").read_text(encoding="utf-8"))
        for tf in ("1h", "4h"):
            pats = st["tf"][tf]["patterns"]
            self.assertEqual(set(pats), {"P0W", "P0M"} | {f"P{i}" for i in range(1, 11)})
            for k, v in pats.items():
                self.assertIn(v["verdict"], ("근거 없음", "이벤트 부족", "합격(전진검증 대상)"), (tf, k))
        self.assertEqual(st["coins"], 18)


if __name__ == "__main__":
    unittest.main()


def test_upcoming_gives_distance_to_both_lines_and_flags_imminent():
    tri = {"pat": "P3", "name": "삼각 수렴 상향 돌파", "dir": 1, "state": "forming", "level": 1.1, "lines": [[[0, 1.2], [9, 1.1]], [[0, 0.8], [9, 0.95]]]}
    far = {"pat": "P9", "name": "박스권", "dir": 1, "state": "forming", "level": 2.0, "lines": []}
    done = {"pat": "P7", "name": "깃발", "dir": 1, "state": "confirmed", "level": 1.0, "lines": []}
    u = pa.upcoming([tri, far, done], 1.0, 0.05)
    assert len(u) == 1 and u[0]["pat"] == "P3"                        # 먼 것·확정된 것 제외
    s = {x["side"]: x for x in u[0]["sides"]}
    assert abs(s["up"]["dist_pct"] - 10.0) < 1e-9 and abs(s["down"]["dist_atr"] - 1.0) < 1e-9 and u[0]["imminent"]
    assert pa.upcoming([tri], 1.0, None) == [] and pa.upcoming([tri], 0, 0.05) == []
