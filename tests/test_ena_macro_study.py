import datetime as dt
import importlib.util
import pathlib
import random
import tempfile
import unittest

P = pathlib.Path(__file__).resolve().parents[1] / "patches/ena-bot/files/ena_macro_study.py"
spec = importlib.util.spec_from_file_location("ms", P); ms = importlib.util.module_from_spec(spec); spec.loader.exec_module(ms)
H = 3_600_000


def hourly_walk(seed, start, days):
    rnd, p, out = random.Random(seed), 1.0, {}
    t0 = int(dt.datetime.fromisoformat(start).replace(tzinfo=dt.timezone.utc).timestamp() * 1000) // H * H
    for h in range(days * 24 + 80):
        p *= 1 + rnd.gauss(0, 0.004)
        out[t0 + h * H] = p
    return out


def weekdays(start, n):
    d, out = dt.date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def index_from(hourly, dates, mix, seed, base=100.0):
    """mix>0 이면 지수 수익률이 '그 뒤 24시간 ENA 수익률'을 섞어 담는다(선행 관계를 심는다)."""
    rnd, v, rows = random.Random(seed), base, []
    for d in dates:
        t = ms.us_close_ms(d)
        p1, pf = ms.price_at(hourly, t), ms.price_at(hourly, t + 24 * H)
        fwd = (pf / p1 - 1) if p1 and pf else 0.0
        v *= 1 + mix * fwd + rnd.gauss(0, 0.01)
        rows.append((d, v))
    return rows


class T(unittest.TestCase):
    def test_parse_fred_skips_header_and_missing(self):
        self.assertEqual(ms.parse_fred("observation_date,X\n2025-01-02,100.5\n2025-01-03,.\n2025-01-06,101\n"), [("2025-01-02", 100.5), ("2025-01-06", 101.0)])

    def test_us_close_follows_daylight_saving(self):
        self.assertEqual(ms.us_close_ms("2025-01-15") % 86_400_000, 21 * H)   # EST: 21:00 UTC
        self.assertEqual(ms.us_close_ms("2025-07-15") % 86_400_000, 20 * H)   # EDT: 20:00 UTC

    def test_price_at_uses_bar_ending_at_that_time(self):
        self.assertEqual(ms.price_at({5 * H: 1.23}, 6 * H), 1.23)
        self.assertIsNone(ms.price_at({5 * H: 1.23}, 7 * H))

    def test_planted_lead_relationship_is_detected(self):
        hourly = hourly_walk(1, "2025-01-01T00:00:00", 600)
        dates = weekdays("2025-01-06", 380)
        rows = index_from(hourly, dates, mix=1.0, seed=2)
        res = ms.study(hourly, {"NASDAQCOM": rows})["NASDAQCOM"]
        self.assertGreater(res["n"], 300)
        self.assertGreater(res["r_fwd"], 0.3)
        self.assertTrue(res["worth"])

    def test_random_index_is_not_flagged(self):
        hourly = hourly_walk(3, "2025-01-01T00:00:00", 600)
        dates = weekdays("2025-01-06", 380)
        data = {sid: index_from(hourly, dates, mix=0.0, seed=10 + i) for i, sid in enumerate(ms.SERIES)}
        res = ms.study(hourly, data)
        self.assertFalse(any(r["worth"] for r in res.values()))
        self.assertIn("근거가 없습니다", ms.render(res))

    def test_short_sample_is_never_worth(self):
        hourly = hourly_walk(1, "2025-01-01T00:00:00", 100)
        rows = index_from(hourly, weekdays("2025-01-06", 60), mix=1.0, seed=2)
        r = ms.study(hourly, {"NASDAQCOM": rows})["NASDAQCOM"]
        self.assertFalse(r["worth"])
        self.assertIn("표본 부족", ms.render({"NASDAQCOM": r}))

    def test_fetch_uses_cache_and_rejects_empty(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            def fetch(u): calls.append(u); return "observation_date,X\n2025-01-02,1\n2025-01-03,2\n"
            ms.fetch_fred("X", pathlib.Path(d), fetch=fetch)
            ms.fetch_fred("X", pathlib.Path(d), fetch=fetch)
            self.assertEqual(len(calls), 1)
            self.assertTrue(calls[0].endswith("id=X&cosd=" + ms.START))   # 전체 이력이 아니라 최근 구간만 요청
            with self.assertRaises(ValueError):
                ms.fetch_fred("Y", pathlib.Path(d), fetch=lambda u: "observation_date,Y\n")


class HttpGet(unittest.TestCase):
    def test_uses_curl_and_reports_failure(self):
        import subprocess
        from unittest import mock
        ok = subprocess.CompletedProcess(["curl"], 0, "a,b\n", "")
        with mock.patch.object(ms.subprocess, "run", return_value=ok) as run:
            self.assertEqual(ms.http_get("https://x"), "a,b\n")
            self.assertEqual(run.call_args[0][0][0], "curl")
            self.assertIn("--http1.1", run.call_args[0][0])
            self.assertNotIn("-A", run.call_args[0][0])
        with mock.patch.object(ms.subprocess, "run", side_effect=subprocess.TimeoutExpired("curl", 35)):
            with self.assertRaises(OSError) as cm:
                ms.http_get("https://x")
            self.assertIn("받지 못했습니다", str(cm.exception))
        bad = subprocess.CompletedProcess(["curl"], 28, "", "timed out")
        with mock.patch.object(ms.subprocess, "run", return_value=bad):
            with self.assertRaises(OSError):
                ms.http_get("https://x")


if __name__ == "__main__":
    unittest.main()
