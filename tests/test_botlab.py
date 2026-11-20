import csv
import json
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from solomon import botlab, policy
from solomon.dashboard import ApiError
from solomon.orchestrator import Orchestrator

from helpers import ScriptedProvider, make_repo, make_settings, order_json, review_json, write_file
from test_dashboard import Env, sleeper

T0 = 1_787_000_000_000


def make_bot(root: Path) -> Path:
    bot = root / "ena-bot"
    (bot / "ena_bot").mkdir(parents=True)
    (bot / "data").mkdir()
    (bot / ".venv" / "bin").mkdir(parents=True)
    py = bot / ".venv" / "bin" / "python"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    con = sqlite3.connect(bot / "data" / "bot.db")
    con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, ts_ms INTEGER, kind TEXT, data TEXT)")
    now = int(time.time() * 1000)
    rows = [(now - 3_600_000, "trend_cycle", "{}"), (now - 1000, "error", "{}"),
            (T0, "ml2_pred", json.dumps({"ts": T0, "p": 0.6, "dir": 1.0})), (T0, "news_score", json.dumps({"ts": T0, "risk": 0.7}))]
    con.executemany("insert into events(ts_ms, kind, data) values (?,?,?)", rows)
    con.commit()
    con.close()
    return bot


class FakeRun:
    """Stands in for subprocess.run: `fetch` writes prices to --out, `launchctl list` prints services."""

    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, args, **kw):
        self.calls.append(list(args))
        if args[0] == "launchctl":
            return subprocess.CompletedProcess(args, 0, "123\t0\tcom.ena.trend\n-\t1\tcom.ena.report\n456\t0\tcom.apple.x\n", "")
        if self.fail:
            return subprocess.CompletedProcess(args, 1, "", "Bybit error 10006: Too many visits")
        out = Path(args[args.index("--out") + 1])
        with open(out, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["ts", "open", "high", "low", "close", "volume"])
            for i in range(700):
                w.writerow([T0 + i * 180_000, 1, 1, 1, 1 + i * 0.001, 1])
        return subprocess.CompletedProcess(args, 0, "ok", "")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def lab(tmp_path, run=None, clock=time.time):
    return botlab.BotLab(tmp_path / "lab", run=run or FakeRun(), clock=clock)


def test_config_rejects_a_folder_that_is_not_the_bot_and_bad_numbers(home):
    L = lab(home)
    with pytest.raises(botlab.BotLabError):
        L.update({"bot_dir": str(home)})
    bot = make_bot(home)
    assert L.update({"bot_dir": str(bot), "interval_hours": 3})["interval_hours"] == 3
    for bad in ({"interval_hours": 0}, {"fetch_days": 500}, {"analyze_hours": "x"}):
        with pytest.raises(botlab.BotLabError):
            L.update(bad)


def test_evaluate_fetches_into_our_folder_scores_and_never_touches_the_bot(home):
    bot = make_bot(home)
    db = bot / "data" / "bot.db"
    before = db.read_bytes()
    run = FakeRun()
    L = lab(home, run)
    L.update({"bot_dir": str(bot)})
    e = L.evaluate()
    assert e["fetch_error"] == "" and e["signals"]["ml2"]["overall"]["n"] == 1 and e["filter"] is None
    assert (home / "lab" / "ENAUSDT_3m.csv").is_file() and L.history()[-1]["ts"] == e["ts"]
    fetch = run.calls[0]
    assert fetch[0].endswith(".venv/bin/python") and "--out" in fetch and str(bot) not in fetch[fetch.index("--out") + 1]
    assert db.read_bytes() == before
    assert not any(p.name.endswith(".csv") for p in (bot / "data").iterdir())


def test_failed_price_fetch_keeps_the_old_prices_and_says_so(home):
    bot = make_bot(home)
    L = lab(home, FakeRun())
    L.update({"bot_dir": str(bot)})
    L.evaluate()
    L._run = FakeRun(fail=True)
    e = L.evaluate()
    assert "10006" in e["fetch_error"] and e["stale_prices"] is True and e["signals"]["ml2"]["overall"]["n"] == 1


def test_health_and_services_are_read_only_views(home):
    bot = make_bot(home)
    L = lab(home)
    h = L.health(str(bot))
    assert h["ok"] and h["alive"] and h["errors_24h"] == 1 and any(k["kind"] == "ml2_pred" for k in h["kinds"])
    sv = L.services()
    assert [i["label"] for i in sv["items"]] == ["com.ena.report", "com.ena.trend"] and sv["items"][1]["running"] and not sv["items"][0]["running"]


def test_schedule_is_due_only_when_configured_and_elapsed(home):
    bot = make_bot(home)
    now = [1_000_000.0]
    L = lab(home, clock=lambda: now[0])
    assert not L.due_eval()
    L.update({"bot_dir": str(bot), "interval_hours": 6, "analyze_hours": 24})
    assert L.due_eval() and L.due_analyze()
    L.evaluate()
    L.mark_analyze()
    assert not L.due_eval() and not L.due_analyze()
    now[0] += 6 * 3600 + 1
    assert L.due_eval() and not L.due_analyze()
    now[0] += 20 * 3600
    assert L.due_analyze()
    L.update({"enabled": False, "auto_analyze": False})
    assert not L.due_eval() and not L.due_analyze()


# ---------------------------------------------------------------- policy enforced by the pipeline itself
def test_project_protection_cannot_be_dropped_by_the_plan_and_blocks_the_change(tmp_path):
    repo = make_repo(tmp_path)
    policy.save(Path(repo), ".solomon", ["src/new.py"], False)
    s = make_settings()
    o = Orchestrator(s, {"claude": ScriptedProvider("claude", [order_json(), review_json()]),
                         "codex": ScriptedProvider("codex", [write_file("src/new.py")] * 6)})
    r = o.run("add module", repo)
    ev = [json.loads(l) for l in (Path(r.run_dir) / "events.jsonl").read_text().splitlines()]
    order = next(e for e in ev if e["event"] == "order")["order"]
    assert "src/new.py" in order["protected_paths"]
    assert r.status != "PASSED" and any("protected path" in json.dumps(e) for e in ev if e["event"] == "scope_violation")


def test_policy_roundtrip_and_bad_patterns(tmp_path):
    with pytest.raises(ValueError):
        policy.save(tmp_path, ".solomon", ["/etc/passwd"], False)
    policy.save(tmp_path, ".solomon", ["data/", "data/", ".env"], True)
    assert policy.load(tmp_path, ".solomon") == {"protect": ["data/", ".env"], "shadow": True, "visual": None}
    assert policy.load(tmp_path / "nope", ".solomon") == {"protect": [], "shadow": False, "visual": None}


def test_policy_shadow_turns_the_arbiter_into_a_recorder(tmp_path):
    repo = make_repo(tmp_path)
    policy.save(Path(repo), ".solomon", [], True)
    s = make_settings()
    s.roles["arbiter"] = "agy"
    agy = ScriptedProvider("agy", [json.dumps({"decision": "REJECT", "confidence": 0.9, "reason": "x", "conditions": []})])
    agy.supports_write = False
    o = Orchestrator(s, {"claude": ScriptedProvider("claude", [order_json(), review_json()]), "codex": ScriptedProvider("codex", [write_file("src/new.py")]), "agy": agy})
    r = o.run("add module", repo)
    assert r.status == "PASSED" and r.arbiter is None
    assert any(json.loads(l)["event"] == "arbiter_shadow" for l in (Path(r.run_dir) / "events.jsonl").read_text().splitlines())


# ---------------------------------------------------------------- the dashboard side
@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sleeper.calls.clear()
    e = Env(tmp_path)
    e.app.botlab._run = FakeRun()
    yield e
    e.close()


def test_connect_evaluate_and_read_the_state_through_the_api(env, tmp_path):
    st, body, _ = env.call("GET", "/api/botlab")
    assert st == 200 and body["config"]["bot_dir"] == "" and body["latest"] is None
    bot = make_bot(tmp_path)
    st, body, _ = env.call("POST", "/api/botlab/config", {"bot_dir": str(bot), "enabled": True})
    assert st == 200 and body["config"]["bot_dir"] == str(bot.resolve()) and body["policy"]["shadow"] is True
    assert ".env" in body["policy"]["protect"] and str(bot.resolve()) in env.app._projects
    assert env.call("POST", "/api/botlab/run", {})[0] == 200
    for _ in range(100):
        time.sleep(0.1)
        st, body, _ = env.call("GET", "/api/botlab")
        if body["latest"]:
            break
    assert body["latest"]["signals"]["ml2"]["overall"]["n"] == 1 and body["health"]["ok"] and len(body["series"]) == 1


def test_policy_endpoint_validates(env, tmp_path):
    bot = make_bot(tmp_path)
    env.call("POST", "/api/botlab/config", {"bot_dir": str(bot)})
    assert env.call("POST", "/api/botlab/policy", {"protect": ["../x"], "shadow": True})[0] == 400
    st, body, _ = env.call("POST", "/api/botlab/policy", {"protect": ["data/", "ena_bot/risk.py"], "shadow": False})
    assert st == 200 and body["policy"] == {"protect": ["data/", "ena_bot/risk.py"], "shadow": False, "visual": None}


def test_the_scheduler_evaluates_then_starts_one_analysis_and_does_not_repeat(env, tmp_path):
    bot = make_bot(tmp_path)
    env.call("POST", "/api/botlab/config", {"bot_dir": str(bot), "interval_hours": 6, "analyze_hours": 24})
    started = []
    env.app.start_analysis = lambda project, focus="", ais=None: started.append((project, focus)) or {}
    env.app.botlab_tick()
    assert len(started) == 1 and started[0][0] == str(bot.resolve()) and "읽기만" in started[0][1] and "표본이 30건 미만" in started[0][1]
    assert env.app.botlab.history()
    env.app.botlab_tick()
    assert len(started) == 1


def test_a_busy_dashboard_postpones_the_analysis_without_marking_it_done(env, tmp_path):
    bot = make_bot(tmp_path)
    env.call("POST", "/api/botlab/config", {"bot_dir": str(bot)})

    def busy(project, focus="", ais=None):
        raise ApiError(409, "busy")

    env.app.start_analysis = busy
    env.app.botlab_tick()
    assert env.app.botlab.config()["last_analyze"] == 0.0  # tried again next minute


def test_the_tab_exists_and_page_script_is_valid():
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    assert 'id="tab-bot"' in html and 'id="view-bot"' in html and "renderBot" in html and "/api/botlab/policy" in html


def test_the_bots_own_monitor_page_is_probed_with_a_short_timeout_and_cached(tmp_path):
    import http.server, threading
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            hits.append(1)
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    now = [1000.0]
    lab = botlab.BotLab(tmp_path / "lab", clock=lambda: now[0])
    r = lab.monitor(port)
    assert r == {"url": f"http://127.0.0.1:{port}/", "up": True}
    lab.monitor(port)
    assert len(hits) == 1  # cached
    srv.shutdown(); srv.server_close()
    now[0] += 11
    assert lab.monitor(port)["up"] is False  # nothing listening any more
