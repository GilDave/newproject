import importlib.util, json, sqlite3, tempfile, unittest, pathlib

P = pathlib.Path(__file__).resolve().parents[1] / "patches/ena-bot/files/ena_postmortem.py"
spec = importlib.util.spec_from_file_location("pm", P); pm = importlib.util.module_from_spec(spec); spec.loader.exec_module(pm)
H = 3_600_000
T0 = 1_760_000_000_000


def cyc(i, price, qty, eq, why=None):
    return {"ts": T0 + i * H, "ts_ms": T0 + i * H, "price": price, "qty": qty, "equity": eq, "why": why or []}


class T(unittest.TestCase):
    def test_round_trips_and_flip(self):
        cs = [cyc(0, 1, 0, 100), cyc(1, 1, 10, 100), cyc(2, 1.1, 10, 101), cyc(3, 1.1, 0, 101),
              cyc(4, 1.1, -10, 101), cyc(5, 1.2, -10, 100), cyc(6, 1.2, 10, 99)]
        t = pm.round_trips(cs)
        self.assertEqual(len(t), 3)
        self.assertEqual((t[0]["side"], round(t[0]["pnl"], 2)), (1, 1.0))
        self.assertEqual((t[1]["side"], round(t[1]["pnl"], 2)), (-1, -2.0))   # 숏: 101→99 (반전 시점 청산)
        self.assertTrue(t[2].get("open"))

    def test_analyze_and_db(self):
        cs = [cyc(0, 1, 0, 100), cyc(1, 1, 10, 100), cyc(2, 0.9, 0, 90, ["halted:max_drawdown"])]
        with tempfile.TemporaryDirectory() as d:
            db = d + "/b.db"
            c = sqlite3.connect(db); c.execute("create table events(id integer primary key, ts_ms int, kind text, data text)")
            for x in cs:
                c.execute("insert into events(ts_ms,kind,data) values(?,?,?)", (x["ts_ms"], "trend_cycle", json.dumps(x)))
            c.execute("insert into events(ts_ms,kind,data) values(?,?,?)", (T0, "news_score", "{}")); c.commit(); c.close()
            s = pm.analyze(pm.load_cycles(db))
        self.assertEqual(s["cycles"], 3); self.assertEqual(s["halted_cycles"], 1)
        self.assertEqual(s["trades"], 1); self.assertEqual(s["losses"], 1)
        self.assertAlmostEqual(s["max_drawdown_pct"], -10.0)
        self.assertIn("왕복 거래 1건", pm.render(s))

    def test_empty(self):
        self.assertEqual(pm.render(pm.analyze([])), "분석할 trend_cycle 기록이 없습니다.")


if __name__ == "__main__":
    unittest.main()


class V1(unittest.TestCase):
    def test_v1_pairing_and_groups(self):
        with tempfile.TemporaryDirectory() as d:
            db = d + "/b.db"
            c = sqlite3.connect(db); c.execute("create table events(id integer primary key, ts_ms int, kind text, data text)")
            def ev(i, k, **dd): c.execute("insert into events(ts_ms,kind,data) values(?,?,?)", (T0 + i * 180000, k, json.dumps(dd)))
            ev(0, "entry", side="Buy", qty=100, price=1.0, stop=0.98, signal="ema_cross_up rsi=61.0")
            ev(1, "trade_closed", pnl=-0.5, side="Buy", bars=3)
            ev(2, "skip", why="x")
            ev(3, "entry", side="Sell", qty=100, price=1.0, stop=1.02, signal="ema_cross_down rsi=44.0")
            ev(4, "trade_closed", pnl=1.0, side="Sell", bars=12)
            ev(5, "trade_closed", pnl=9.9, side="Sell", bars=1)   # 짝 없는 청산은 무시
            c.commit(); c.close()
            s = pm.analyze_v1(pm.load_v1(db))
        self.assertEqual((s["trades"], s["wins"], s["losses"]), (2, 1, 1))
        self.assertEqual(s["by_signal"]["ema_cross_up"]["n"], 1)
        self.assertEqual(s["by_rsi"]["rsi>60"]["n"], 1)
        self.assertEqual(s["median_stop_pct"], 2.0)
        self.assertEqual(s["breakeven_win_rate_pct"], 33.3)   # 평균 이익 1.0, 평균 손실 0.5 → 0.5/1.5
        self.assertEqual(len(s["reading_notes"]), 4)
        self.assertIn("표본 크기", pm.PROMPT)
        self.assertIn("거래 2건", pm.render_v1(s))
        self.assertEqual(pm.render_v1(pm.analyze_v1([])), "분석할 entry/trade_closed 기록이 없습니다.")
