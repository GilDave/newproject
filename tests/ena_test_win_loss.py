"""ENA 저장소에서 실행(win_loss.py 를 ena_bot/ 에 두고 wire_win_loss.py 적용 후 tests/ 로 복사)."""
import json
import re
import sqlite3
import subprocess
from pathlib import Path

from ena_bot import win_loss as wl

H4 = 4 * 3_600_000
PAGE = Path(__file__).resolve().parents[1] / "ena_bot/dashboard_static/index.html"


def ev(i, qty, eq, price=1.0):
    return {"ts": i * H4, "qty": qty, "equity": eq, "price": price}


def test_episodes_count_open_close_flip_and_hold_open_trade_aside():
    seq = [ev(0, 0, 100), ev(1, 10, 99.9), ev(2, 10, 103), ev(3, 0, 102.5),           # 롱 +2.5 (시작 자본 100)
           ev(4, 0, 102.5), ev(5, -5, 102.4), ev(6, -5, 101), ev(7, 4, 100.9),         # 숏 → 롱 반전: 숏 -1.6
           ev(8, 4, 101.5)]                                                            # 롱 열려 있음
    done, open_ = wl.episodes(seq)
    assert [round(t["pnl"], 2) for t in done] == [2.5, -1.6] and [t["side"] for t in done] == ["long", "short"]
    assert open_ and open_["side"] == "long" and abs(open_["pnl"] - 0.6) < 1e-9


def test_position_already_open_at_first_event_is_not_counted():
    done, open_ = wl.episodes([ev(0, 5, 100), ev(1, 5, 90), ev(2, 0, 95)])
    assert done == [] and open_ is None


def test_summary_numbers_and_wilson_interval():
    trades = [{"pnl": 4.0}, {"pnl": -1.0}, {"pnl": -1.0}, {"pnl": 2.0}, {"pnl": 0.0}]
    s = wl.summarize(trades)
    assert (s["wins"], s["losses"], s["win_rate"]) == (2, 3, 40.0) and s["max_losing_streak"] == 2 and not s["enough"]
    assert abs(s["payoff"] - 3.0 / (2 / 3)) < 1e-9 and abs(s["profit_factor"] - 6 / 2) < 1e-9 and s["recent"] == ["W", "L", "L", "W", "L"]
    lo, hi = wl.wilson(2, 5)
    assert lo < 40 < hi and wl.wilson(0, 0) is None
    assert wl.summarize([])["win_rate"] is None


def make_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, ts_ms INTEGER, kind TEXT, data TEXT)")
    for k, d in rows:
        con.execute("INSERT INTO events (ts_ms, kind, data) VALUES (1, ?, ?)", (k, json.dumps(d)))
    con.commit(); con.close()


def test_payload_reads_db_read_only_and_survives_missing_or_broken(tmp_path):
    assert wl.payload(tmp_path / "none.db")["ok"] is False
    rows = [("trend_cycle", ev(i, q, e)) for i, (q, e) in enumerate([(0, 100), (3, 99), (3, 104), (0, 103)])] + [("trade_closed", {"pnl": -0.5, "side": "long", "bars": 3}), ("trend_cycle", {"junk": 1})]
    make_db(tmp_path / "bot.db", rows)
    r = wl.payload(tmp_path / "bot.db")
    a = r["accounts"][0]
    assert r["ok"] and a["key"] == "base" and (a["wins"], a["losses"]) == (1, 0) and r["legacy"]["losses"] == 1
    (tmp_path / "bad.db").write_bytes(b"not a database")
    assert wl.payload(tmp_path / "bad.db")["ok"] is False


def test_wire_adds_tab_endpoint_and_valid_script_idempotently(tmp_path):
    root = Path(__file__).resolve().parents[1]
    for rel in ("ena_bot/dashboard.py", "ena_bot/dashboard_static/index.html", "ena_bot/win_loss.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes((root / rel).read_bytes())
    wire = root / "wire_win_loss.py"
    r = subprocess.run(["python3", str(wire), str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    page = (tmp_path / "ena_bot/dashboard_static/index.html").read_text(encoding="utf-8")
    assert "['wl', '승률 Win vs Lose']" in page and "/api/win-loss" in (tmp_path / "ena_bot/dashboard.py").read_text(encoding="utf-8")
    for i, m in enumerate(re.finditer(r"<script>(.*?)</script>", page, re.S)):
        (tmp_path / f"s{i}.js").write_text(m.group(1))
        assert subprocess.run(["node", "--check", str(tmp_path / f"s{i}.js")]).returncode == 0
    assert "이미" in subprocess.run(["python3", str(wire), str(tmp_path)], capture_output=True, text=True).stdout
