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


def test_price_fetch_asks_only_for_the_days_the_scoring_needs_and_keeps_off_the_bar_close(home):
    bot = make_bot(home)
    now = [T0 / 1000 + 3 * 86400 + 5000]  # the oldest scored event is 3 days (+) old
    L = botlab.BotLab(home / "lab", run=FakeRun(), clock=lambda: now[0])
    days = L.needed_days(bot / "data" / "bot.db", 30)
    assert 5.0 <= days <= 6.0  # 3 days + 2 days margin, never below 5
    assert L.needed_days(bot / "data" / "bot.db", 4) == 4.0  # never above the cap
    assert L.needed_days(home / "missing.db", 30) == 30.0  # unreadable: the old behaviour
    L.update({"bot_dir": str(bot), "interval_hours": 6})
    now[0] = 1_800_000_000 - (1_800_000_000 % 14400) + 120  # two minutes after a 4-hour bar closed
    assert not L.due_eval()
    now[0] += 300
    assert L.due_eval()
    run = FakeRun()
    L._run = run
    now[0] = T0 / 1000 + 3 * 86400 + 5000
    L.evaluate()
    assert "--days" in run.calls[0] and int(run.calls[0][run.calls[0].index("--days") + 1]) < 30


# ---------------------------------------------------------------- restarting the bot's services
class LaunchctlRun:
    """`launchctl list` shows the ENA services; kickstart records what it was asked and can fail for one label."""

    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def __call__(self, args, **kw):
        self.calls.append(list(args))
        if args[:2] == ["launchctl", "list"]:
            return subprocess.CompletedProcess(args, 0, "11\t0\tcom.ena.trend\n12\t0\tcom.ena.dashboard\n-\t0\tcom.ena.report\n99\t0\tcom.apple.x\n", "")
        label = args[-1].split("/")[-1]
        if label in self.fail:
            return subprocess.CompletedProcess(args, 113, "", "Could not find service")
        return subprocess.CompletedProcess(args, 0, "", "")


def test_the_engine_is_restarted_last_and_only_known_ena_services_can_be_touched(tmp_path):
    import os
    from solomon import botlab as B
    run = LaunchctlRun()
    lab = B.BotLab(tmp_path / "lab", run=run)
    lab.monitor = lambda port=8787: {"up": True}  # no real network
    res = lab.restart_services(["com.ena.trend", "com.ena.dashboard", "com.ena.report"], sleep=lambda s: None)
    kick = [c for c in run.calls if c[:3] == ["launchctl", "kickstart", "-k"]]
    assert [c[-1].split("/")[-1] for c in kick] == ["com.ena.report", "com.ena.dashboard", "com.ena.trend"]  # the trading engine goes last
    assert all(c[-1].startswith(f"gui/{os.getuid()}/com.ena.") for c in kick)
    assert all(r["ok"] for r in res["results"]) and {r["label"]: r["running"] for r in res["results"]} == {"com.ena.report": False, "com.ena.dashboard": True, "com.ena.trend": True}
    for bad in (["com.apple.x"], ["com.ena.trend; rm"], ["com.ena.unknown"], [], "com.ena.trend", ["../com.ena.trend"], [1], ["com.ena.trend"] * 9):
        with pytest.raises(B.BotLabError):
            lab.restart_services(bad, sleep=lambda s: None)
    assert not any("com.apple" in " ".join(c) for c in kick)


def test_a_service_that_cannot_be_restarted_is_reported_and_the_others_still_are(tmp_path):
    from solomon import botlab as B
    lab = B.BotLab(tmp_path / "lab", run=LaunchctlRun(fail=["com.ena.dashboard"]))
    lab.monitor = lambda port=8787: {"up": False}
    res = lab.restart_services(["com.ena.dashboard", "com.ena.trend"], sleep=lambda s: None)
    by = {r["label"]: r for r in res["results"]}
    assert by["com.ena.dashboard"]["ok"] is False and "Could not find service" in by["com.ena.dashboard"]["error"] and by["com.ena.trend"]["ok"] is True
    assert res["monitor"] is False


def test_without_launchctl_it_says_so(tmp_path):
    from solomon import botlab as B

    def boom(args, **kw):
        raise OSError("no launchctl")
    with pytest.raises(B.BotLabError, match="launchctl"):
        B.BotLab(tmp_path / "lab", run=boom).restart_services(["com.ena.trend"], sleep=lambda s: None)


def test_dashboard_endpoint_and_buttons(tmp_path, monkeypatch):
    from test_dashboard import Env
    e = Env(tmp_path)
    try:
        lab = e.app.botlab
        lab._run = LaunchctlRun()
        lab.monitor = lambda port=8787: {"url": "http://127.0.0.1:8787/", "up": True}  # no real network in the test
        monkeypatch.setattr("time.sleep", lambda s: None)
        st, body, _ = e.call("POST", "/api/botlab/restart", {"labels": ["com.ena.dashboard", "com.ena.trend"]})
        assert st == 200 and "재시작했습니다: com.ena.dashboard, com.ena.trend" in body["message"] and "모니터(8787)" in body["message"]
        assert e.call("POST", "/api/botlab/restart", {"labels": ["com.apple.x"]})[0] == 400
        assert e.call("POST", "/api/botlab/restart", {"labels": []})[0] == 400
        assert e.call("POST", "/api/botlab/restart", {"labels": ["com.ena.trend"]}, token=None)[0] == 401
    finally:
        e.close()
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    for needle in ('id="t-ena-restart"', "ENA 봇 재시작", '"/api/botlab/restart"', "openEnaRestart", "com.ena.trend", "실거래 모드입니다", "거래 엔진은 맨 마지막"):
        assert needle in html, needle
    assert html.index('id="t-restart"') < html.index('id="t-ena-restart"')  # to the right of the dashboard's own restart button


def _ps_run(ps_text, lc_text="100\t0\tcom.ena.trend\n"):
    def run(args, **kw):
        if args[0] == "ps":
            return subprocess.CompletedProcess(args, 0, ps_text, "")
        if args[0] == "launchctl":
            return subprocess.CompletedProcess(args, 0, lc_text, "")
        raise AssertionError(args)
    return run


PY = "/usr/bin/python3 -m ena_bot --config /b/config.json"


def test_unmanaged_ena_processes_are_listed_but_launchd_ones_and_their_children_are_not(tmp_path):
    ps = (f"  100     1 05:00:00 caffeinate -i {PY} trend-run\n"
          f"  101   100 05:00:00 /b/.venv/bin/python -m ena_bot --config /b/config.json trend-run\n"
          f"  200     1 7-01:10:00 {PY} run --env /b/.env\n"
          f"  300     1 01:00 /usr/bin/python3 -m pytest\n"
          f"  400     1 02:00 caffeinate -i /usr/bin/python3 -m ena_bot dashboard\n")
    L = lab(tmp_path, _ps_run(ps))
    out = L.stray_processes()
    assert out["available"] is True
    by_pid = {i["pid"]: i for i in out["items"]}
    assert set(by_pid) == {200}
    assert by_pid[200]["command"] == "run" and by_pid[200]["running_for"] == "7-01:10:00" and by_pid[200]["writer"]


def test_a_failing_ps_never_breaks_the_page(tmp_path):
    def boom(args, **kw):
        raise OSError("no ps")
    assert lab(tmp_path, boom).stray_processes() == {"available": False, "items": []}
    assert lab(tmp_path).stray_processes()["available"] is False  # the shared FakeRun cannot answer ps: tolerated


def test_nothing_stray_means_an_empty_list(tmp_path):
    assert lab(tmp_path, _ps_run("  100     1 05:00:00 caffeinate -i x\n")).stray_processes() == {"available": True, "items": []}
