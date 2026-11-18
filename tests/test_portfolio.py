import json
from urllib.parse import quote

import pytest

from solomon import portfolio
from solomon.orchestrator import Orchestrator

from helpers import ScriptedProvider, make_settings, order_json, review_json, write_file
from test_dashboard import Env, sleeper
from test_autofix import cluster, put_analysis
from test_manage import make_batch


@pytest.fixture
def env(tmp_path):
    sleeper.calls.clear()
    e = Env(tmp_path)
    yield e
    e.close()


def board(env):
    st, body, _ = env.call("GET", "/api/portfolio")
    assert st == 200, body
    return body


def card(env):
    return next(c for c in board(env)["projects"] if c["path"] == env.project)


def test_empty_project_suggests_starting_with_an_analysis(env):
    c = card(env)
    assert c["counts"]["runs"] == 0 and c["pass_rate"] is None and c["next"]["action"] == "analyze"
    assert c["is_git"] and c["branch"] and c["ais"] == {}


def test_card_numbers_come_from_the_logs(env):
    r1 = env.finish_a_run()
    s = make_settings(max_attempts=1)
    o = Orchestrator(s, {"claude": ScriptedProvider("claude", [order_json(), review_json("FAIL", ["no"])]),
                         "codex": ScriptedProvider("codex", [write_file("src/other.py")])})
    r2 = o.run("second", env.repo)
    assert r1.status == "PASSED" and r2.status == "FAILED"
    c = card(env)
    assert c["counts"]["runs"] == 2 and c["counts"]["passed"] == 1 and c["counts"]["failed"] == 1
    assert c["pass_rate"] == 50 and c["avg_attempts"] == 1.0
    assert c["recent"] == ["PASSED", "FAILED"] and c["last_run"]["status"] == "FAILED"
    assert c["counts"]["pending"] == 1 and c["next"]["action"] == "manage"
    claude, codex = c["ais"]["claude"], c["ais"]["codex"]
    assert claude["calls"] == 4 and claude["roles"] == {"planner": 2, "reviewer": 2}  # 2 runs x (plan + review)
    assert claude["reviews"] == {"pass": 1, "fail": 1}
    assert codex["calls"] == 2 and codex["builds"] == {"attempts": 2, "passed": 1}
    assert claude["tokens"] > 0 and codex["tokens"] > 0
    assert c["tokens"] == claude["tokens"] + codex["tokens"]


def test_multi_ai_participation_shows_up_per_ai(env):
    s = make_settings()
    s.roles.update(planner2="antigravity", reviewer2="antigravity")
    agy = ScriptedProvider("antigravity", [json.dumps({"verdict": "OK", "concerns": [], "extra_acceptance_criteria": ["x"]}), review_json()])
    agy.supports_write = False
    o = Orchestrator(s, {"claude": ScriptedProvider("claude", [order_json(), review_json()]),
                         "codex": ScriptedProvider("codex", [write_file("src/new.py")]), "antigravity": agy})
    assert o.run("three ais", env.repo).status == "PASSED"
    a = card(env)["ais"]["antigravity"]
    assert a["calls"] == 2 and a["roles"] == {"planner2": 1, "reviewer2": 1} and a["reviews"]["pass"] == 1


def test_analyses_batches_and_the_stage_counts(env):
    b = make_batch(env)
    c = card(env)
    assert c["last_analysis"]["findings"] == 2  # put_analysis wrote 2 clusters
    assert c["counts"]["batches"] == 1 and c["batches"][0]["fixed"] == 2
    assert c["counts"]["pending"] == 1  # the batch (its item runs are not counted twice)
    assert card(env)["next"]["action"] == "manage"


def test_running_job_and_totals_and_cache_invalidation(env):
    env.call("POST", "/api/analyze", {"project": env.project})
    c = card(env)
    assert c["busy"] and c["next"]["action"] == "work"
    t = board(env)["totals"]
    assert t["projects"] == 1 and t["busy"] == 1


def test_a_broken_project_does_not_blank_the_board(env, monkeypatch):
    monkeypatch.setattr(portfolio, "card", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    env.app._portfolio_cache = None
    st, body, _ = env.call("GET", "/api/portfolio")
    assert st == 200 and "boom" in body["projects"][0]["error"]


def test_portfolio_needs_the_token(env):
    assert env.call("GET", "/api/portfolio", token=None)[0] == 401
