import csv
import json
import sqlite3
from pathlib import Path

import pytest

from solomon import cli, shadow
from solomon.settings import load_settings

from helpers import ScriptedProvider, make_repo, make_settings, order_json, review_json, write_file
from solomon.orchestrator import Orchestrator


def verdict(d="ADOPT", c=0.9):
    return json.dumps({"decision": d, "confidence": c, "reason": "r", "conditions": []})


def build(arbiter, claude, codex):
    s = make_settings()
    s.roles["arbiter"] = "agy"
    s.arbiter_shadow = True
    agy = ScriptedProvider("agy", list(arbiter))
    agy.supports_write = False
    return Orchestrator(s, {"claude": ScriptedProvider("claude", claude), "codex": ScriptedProvider("codex", codex), "agy": agy}), agy


def events(r):
    return [json.loads(l) for l in (Path(r.run_dir) / "events.jsonl").read_text().splitlines()]


def test_shadow_answer_is_recorded_but_never_obeyed(tmp_path):
    repo = make_repo(tmp_path)
    o, agy = build([verdict("REJECT", 0.99)], [order_json(), review_json()], [write_file("src/new.py")])
    r = o.run("add module", repo)
    assert r.status == "PASSED" and r.arbiter is None and not r.needs_human  # a REJECT changed nothing
    ev = [e for e in events(r) if e["event"] == "arbiter_shadow"]
    assert len(ev) == 1 and ev[0]["decision"] == "REJECT" and ev[0]["tests"] == "pass" and ev[0]["votes"][0]["verdict"] == "PASS"
    assert not [e for e in events(r) if e["event"] == "arbiter"]
    assert "may have FAILED" in agy.calls[0]["prompt"]


def test_shadow_judges_failed_attempts_too_and_they_are_labelled_by_measurement(tmp_path):
    repo = make_repo(tmp_path)
    o, _ = build([verdict("ADOPT", 0.9), verdict("ADOPT", 0.9)], [order_json(), review_json(), review_json()],
                 [write_file("src/new.py", "raise SystemExit(1)\n"), write_file("src/new.py", "x = 1\n")])
    r = o.run("add module", repo)
    assert r.attempts >= 1
    cases = shadow.collect(Path(repo), o.s.state_dir)
    assert cases, "shadow cases were collected"
    failed = [c for c in cases if c["tests"] == "fail"]
    for c in failed:
        assert c["truth"] == "REJECT" and c["basis"] == "gates failed"


def mk(dec, truth, tests="pass", votes="2/2 PASS", n=1):
    return [{"decision": dec, "truth": truth, "tests": tests, "votes": votes, "confidence": 0.8} for _ in range(n)]


def test_matrix_lists_every_combination_and_never_counts_unknown():
    cases = mk("ADOPT", "ADOPT", n=20) + mk("ADOPT", "REJECT", n=1) + mk("REJECT", "REJECT", "fail", "0/2 PASS", 6) + mk("REJECT", "ADOPT", n=2) \
        + mk("NEED_HUMAN", "ADOPT", n=3) + mk("ADOPT", "unknown", n=5)
    m = shadow.matrix(cases)
    assert m["cases"] == 37 and m["totals"]["unknown"] == 5 and m["totals"]["dangerous"] == 1 and m["totals"]["overcautious"] == 2
    assert m["totals"]["asked_human"] == 3 and m["accuracy"]["of"] == 29 and m["accuracy"]["right"] == 26
    keys = {(r["tests"], r["votes"], r["decision"]) for r in m["cells"]}
    assert ("pass", "2/2 PASS", "ADOPT") in keys and ("fail", "0/2 PASS", "REJECT") in keys
    assert m["danger"]["of_adopts"] == 21 and m["enough_data"] is True


def test_not_enough_data_gives_no_verdict_and_danger_blocks_promotion():
    assert shadow.matrix(mk("ADOPT", "ADOPT", n=5))["ready_to_discuss_promotion"] is False
    assert "부족" in shadow.matrix(mk("ADOPT", "ADOPT", n=5))["note"]
    bad = shadow.matrix(mk("ADOPT", "ADOPT", n=40) + mk("ADOPT", "REJECT", n=10))
    assert bad["enough_data"] and not bad["ready_to_discuss_promotion"]
    good = shadow.matrix(mk("ADOPT", "ADOPT", n=300))
    assert good["ready_to_discuss_promotion"] is True


def test_wilson_is_sane():
    lo, hi = shadow.wilson(0, 0)
    assert (lo, hi) == (0.0, 1.0)
    lo, hi = shadow.wilson(5, 10)
    assert 0.2 < lo < 0.5 < hi < 0.8


def bot_db(tmp_path, rows):
    p = tmp_path / "bot.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, ts_ms INTEGER, kind TEXT, data TEXT)")
    con.executemany("insert into events(ts_ms, kind, data) values (?,?,?)", rows)
    con.commit()
    con.close()
    return p


def candles(tmp_path, prices):
    p = tmp_path / "c.csv"
    with open(p, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ts_ms", "open", "high", "low", "close"])
        for i, c in enumerate(prices):
            w.writerow([1_787_000_000_000 + i * 180_000, c, c, c, c])
    return p


def test_bot_filter_evaluation_counterfactual_and_filter_off_rate(tmp_path):
    prices = [100.0] * 5 + [100.0 + i for i in range(1, 40)]  # rising
    t0 = 1_787_000_000_000
    d = lambda side, block, conf, reason="r": json.dumps({"side": side, "block": block, "confidence": conf, "reason": reason})
    rows = [(t0, "llm", d("long", False, 0.7)),            # allowed, price rises -> good allow
            (t0 + 180_000, "llm", d("short", True, 0.9)),  # blocked short in a rising market -> good block
            (t0 + 360_000, "llm", d("long", True, 0.8)),   # blocked long that would have won -> bad block
            (t0 + 540_000, "llm", d("long", False, 0, "llm_unavailable_or_invalid")),
            (t0 + 600_000, "other", "{}")]
    db = bot_db(tmp_path, rows)
    before = db.read_bytes()
    out = shadow.evaluate_bot_filter(db, candles(tmp_path, prices), bars=5, fee=0.0)
    assert out["events"] == 4 and out["filter_off"] == 1 and out["filter_off_rate"] == 0.25
    assert out["blocked"]["n"] == 2 and out["blocked"]["good"] == 1 and out["allowed"]["good"] == 1
    assert out["blocked"]["enough"] is False and "근사" in out["caveat"]
    assert db.read_bytes() == before  # never written


def test_bot_db_is_opened_read_only(tmp_path):
    db = bot_db(tmp_path, [])
    con = shadow._open_ro(db)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("insert into events(ts_ms, kind, data) values (1,'x','{}')")
    con.close()


def test_cli_prints_json(tmp_path, capsys):
    db = bot_db(tmp_path, [])
    assert cli.main(["shadow", "--bot-db", str(db)]) == 0
    assert json.loads(capsys.readouterr().out)["events"] == 0


def test_bot_signals_scored_only_when_price_covers_the_horizon(tmp_path):
    t0 = 1_787_000_000_000
    prices = [100.0] * 3 + [100.0 + i * 0.1 for i in range(1, 60)]  # drifting up
    ev = lambda kind, **d: (t0, kind, json.dumps(d))
    rows = [ev("ml2_pred", ts=t0, p=0.7, dir=1.0), ev("ml2_pred", ts=t0, p=0.3, dir=-1.0),
            ev("ml2_pred", ts=t0 + 100 * 180_000, p=0.6, dir=1.0),  # price not available that far -> waiting
            ev("news_score", ts=t0, risk=0.8), ev("news_score", ts=t0, risk=0.1)]
    db = bot_db(tmp_path, rows)
    before = db.read_bytes()
    out = shadow.evaluate_bot_signals(db, candles(tmp_path, prices), horizon_bars=20)
    assert out["waiting_for_price"] == 1
    assert out["ml2"]["overall"]["n"] == 2 and out["ml2"]["overall"]["hits"] == 1 and out["ml2"]["overall"]["enough"] is False
    assert out["news"]["high_risk"]["n"] == 1 and out["news"]["low_risk"]["n"] == 1 and out["news"]["enough"] is False
    assert db.read_bytes() == before


def test_shadow_mode_does_not_spend_a_call_on_every_failed_attempt(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings()
    s.roles["arbiter"] = "agy"
    s.arbiter_shadow = True
    s.max_attempts = 6
    agy = ScriptedProvider("agy", [verdict("ADOPT", 0.9)] * 6)
    agy.supports_write = False
    ps = {"claude": ScriptedProvider("claude", [order_json()]), "codex": ScriptedProvider("codex", [write_file("src/other.py", f"x = {i}\n") for i in range(6)]), "agy": agy}
    r = Orchestrator(s, ps, sleep=lambda x: None).run("add module", repo)
    assert r.status == "FAILED" and r.attempts == 6
    assert len([e for e in events(r) if e["event"] == "arbiter_shadow"]) == 2  # two samples of failures, not six


def test_waiting_events_are_listed_with_when_they_can_be_scored(tmp_path):
    t0 = 1_787_000_000_000
    prices = [100.0 + i * 0.1 for i in range(60)]
    rows = [(t0, "ml2_pred", json.dumps({"ts": t0, "p": 0.6, "dir": 1.0})),
            (t0 + 100 * 180_000, "ml2_pred", json.dumps({"ts": t0 + 100 * 180_000, "p": 0.5, "dir": -1.0})),  # beyond the prices
            (t0 + 120 * 180_000, "news_score", json.dumps({"ts": t0 + 120 * 180_000, "risk": 0.7}))]
    db = bot_db(tmp_path, rows)
    now = t0 + 130 * 180_000
    out = shadow.evaluate_bot_signals(db, candles(tmp_path, prices), horizon_bars=20, now_ms=now)
    w = out["waiting_items"]
    assert [x["kind"] for x in w] == ["ml2_pred", "news_score"] and out["waiting_for_price"] == 2
    assert w[0]["ready_ms"] == t0 + 120 * 180_000 and w[1]["ready_ms"] == t0 + 140 * 180_000
    assert w[0]["hours_left"] == 0.0 and w[1]["hours_left"] == round(10 * 180_000 / 3_600_000, 1)  # already coverable vs 30 minutes from now


def test_spearman_handles_ties_signs_and_too_little_data():
    r, ci = shadow.spearman([1, 2, 3, 4, 5, 6], [10, 20, 30, 40, 50, 60])
    assert r > 0.99 and ci is not None
    assert shadow.spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1])[0] < -0.99
    r2, _ = shadow.spearman([1, 1, 2, 2, 3, 3], [1, 2, 1, 2, 1, 2])
    assert abs(r2) < 0.5
    assert shadow.spearman([1, 1, 1], [1, 2, 3]) is None and shadow.spearman([1, 2], [1, 2]) is None
    assert shadow.spearman([1, 2, 3], [1, 2, 3])[1] is None  # an interval needs more than three points


def test_news_score_is_judged_on_all_scored_events_not_only_the_rare_high_ones(tmp_path):
    t0 = 1_787_000_000_000
    prices = [100.0 - i * (0.02 if i % 7 else 0.2) for i in range(700)]
    rows = [(t0 + i * 180_000, "news_score", json.dumps({"ts": t0 + i * 180_000, "risk": 0.05 + (i % 9) * 0.045})) for i in range(0, 40)]
    db = bot_db(tmp_path, rows)
    out = shadow.evaluate_bot_signals(db, candles(tmp_path, prices), horizon_bars=30)
    c = out["news"]["corr"]
    assert c["n"] == 40 and c["enough"] is True and c["rho"] is not None and c["ci"] is not None
    assert out["news"]["enough"] is True and out["news"]["high_risk"]["n"] == 0  # no score reached 0.5, yet the judgement is possible
