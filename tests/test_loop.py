import json
from pathlib import Path

import pytest

from solomon.orchestrator import Orchestrator
from solomon.settings import ConfigError, load_settings

from helpers import ScriptedProvider, make_repo, make_settings, order_json, review_json, write_file


def three(settings, claude, codex, agy=None, **kw):
    provs = {"claude": ScriptedProvider("claude", claude), "codex": ScriptedProvider("codex", codex)}
    if agy is not None:
        provs["agy"] = ScriptedProvider("agy", agy)
    return Orchestrator(settings, provs, **kw), provs


def events(result):
    return [json.loads(l) for l in (Path(result.run_dir) / "events.jsonl").read_text().splitlines()]


def test_unlimited_attempts_keep_going_until_it_passes(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings(max_attempts=0, stagnation_limit=50)
    bad = [write_file("src/other.py", f"x = {i}\n") for i in range(5)]
    orch, p = three(s, [order_json()] + [review_json("FAIL", [f"p{i}"]) for i in range(5)] + [review_json()],
                    bad + [write_file("src/new.py")])
    r = orch.run("goal", repo)
    assert r.status == "PASSED" and r.attempts == 6


def test_attempt_cap_still_applies(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings(max_attempts=2, stagnation_limit=50)
    orch, p = three(s, [order_json(), review_json("FAIL", ["a"]), review_json("FAIL", ["b"])],
                    [write_file("src/other.py", "x=1\n"), write_file("src/other.py", "x=2\n")])
    r = orch.run("goal", repo)
    assert r.status == "FAILED" and r.attempts == 2 and "시도 상한" in r.summary


def test_token_budget_stops_before_another_build(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings(max_attempts=0, max_tokens=1)
    orch, p = three(s, [order_json()], [])
    r = orch.run("goal", repo)
    assert r.status == "FAILED" and "토큰 예산" in r.summary
    assert p["codex"].calls == []


def test_time_budget_with_fake_clock(tmp_path):
    repo = make_repo(tmp_path)
    t = {"now": 0.0}

    def clock():
        t["now"] += 400.0  # every look at the clock costs 400s
        return t["now"]

    s = make_settings(max_attempts=0, max_run_minutes=10, stagnation_limit=50)
    orch, p = three(s, [order_json()] + [review_json("FAIL", ["x"])] * 10,
                    [write_file("src/other.py", f"x={i}\n") for i in range(10)], clock=clock)
    r = orch.run("goal", repo)
    assert r.status == "FAILED" and "시간 예산" in r.summary


def test_stagnation_restarts_then_gives_up(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings(max_attempts=0, stagnation_limit=2, max_restarts=0)
    orch, p = three(s, [order_json()] + [review_json("FAIL", ["same"])] * 5,
                    [write_file("src/other.py", f"x={i}\n") for i in range(5)])
    r = orch.run("goal", repo)
    assert r.status == "FAILED" and "진전 없음" in r.summary and r.attempts == 2


def test_stagnation_restart_tells_builder_to_change_approach(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings(max_attempts=0, stagnation_limit=2, max_restarts=2)
    orch, p = three(s, [order_json()] + [review_json("FAIL", ["same"])] * 2 + [review_json()],
                    [write_file("src/other.py", "x=1\n"), write_file("src/other.py", "x=2\n"), write_file("src/new.py")])
    r = orch.run("goal", repo)
    assert r.status == "PASSED"
    assert "COMPLETELY DIFFERENT" in p["codex"].calls[2]["prompt"]
    assert "stagnation" in [e["event"] for e in events(r)]


def test_baseline_failure_stops_without_calling_the_builder(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings(baseline_check=True)  # src/new.py does not exist at base: the tests fail on untouched code
    orch, p = three(s, [order_json()], [])
    r = orch.run("goal", repo)
    assert r.status == "ERROR" and "수정하기 전" in r.summary
    assert p["codex"].calls == []


def test_flaky_pass_is_caught_by_the_double_check(tmp_path):
    repo = make_repo(tmp_path)
    counter = tmp_path / "count.txt"
    s = make_settings()
    s.test_command = ["python3", "-c",
                      "import pathlib,sys\n"
                      f"c=pathlib.Path({str(counter)!r}); n=int(c.read_text()) if c.exists() else 0; n+=1; c.write_text(str(n))\n"
                      "sys.exit(0 if pathlib.Path('src/new.py').exists() and n!=2 else 1)"]
    orch, p = three(s, [order_json(), review_json(), review_json()],
                    [write_file("src/new.py"), write_file("src/new.py", "x = 2\n")])
    r = orch.run("goal", repo)
    assert r.status == "PASSED" and r.attempts == 2
    assert "FAILED when run again" in p["codex"].calls[1]["prompt"]
    assert [e["passed"] for e in events(r) if e["event"] == "double_check"] == [False, True]


def test_second_reviewer_must_also_pass(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings()
    s.roles["reviewer2"] = "agy"
    orch, p = three(s, [order_json(), review_json(), review_json()], [write_file("src/new.py"), write_file("src/new.py", "x=2\n")],
                    agy=[review_json("FAIL", ["agy found a bug"]), review_json()])
    r = orch.run("goal", repo)
    # attempt 1: claude PASS, agy FAIL -> rebuild; attempt 2: claude must be asked again
    assert r.status == "PASSED" and r.attempts == 2 and len(p["agy"].calls) == 2
    assert "agy found a bug" in p["codex"].calls[1]["prompt"] and "[agy]" in p["codex"].calls[1]["prompt"]


def test_second_reviewer_is_skipped_when_the_first_fails(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings(max_attempts=1)
    s.roles["reviewer2"] = "agy"
    orch, p = three(s, [order_json(), review_json("FAIL", ["nope"])], [write_file("src/new.py")], agy=[])
    r = orch.run("goal", repo)
    assert r.status == "FAILED" and p["agy"].calls == []


def test_second_reviewer_without_verdict_is_an_error_never_a_pass(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings()
    s.roles["reviewer2"] = "agy"
    orch, p = three(s, [order_json(), review_json()], [write_file("src/new.py")], agy=["junk", "junk", "junk"])
    r = orch.run("goal", repo)
    assert r.status == "ERROR" and r.patch_path is None and len(p["codex"].calls) == 1


def test_second_reviewer_cannot_be_the_builder():
    s = make_settings()
    s.roles["reviewer2"] = "codex"
    with pytest.raises(ConfigError):
        s.validate()


def test_loop_settings_from_toml(tmp_path):
    f = tmp_path / "s.toml"
    f.write_text("[loop]\nmax_attempts=0\nmax_run_minutes=90\nmax_tokens=500000\nstagnation_limit=4\n"
                 "max_restarts=1\ndouble_check_tests=false\nbaseline_check=false\n")
    s = load_settings(f)
    assert (s.max_attempts, s.max_run_minutes, s.max_tokens, s.stagnation_limit, s.max_restarts) == (0, 90.0, 500000, 4, 1)
    assert s.double_check_tests is False and s.baseline_check is False
    f.write_text("[loop]\nstagnation_limit=1\n")
    with pytest.raises(ConfigError):
        load_settings(f)
