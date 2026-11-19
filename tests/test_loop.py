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
    orch, p = three(s, [order_json(), review_json()],  # failing attempts are not reviewed: only the passing one is
                    bad + [write_file("src/new.py")])
    r = orch.run("goal", repo)
    assert r.status == "PASSED" and r.attempts == 6


def test_attempt_cap_still_applies(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings(max_attempts=2, stagnation_limit=50)
    orch, p = three(s, [order_json()],
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
    orch, p = three(s, [order_json(), review_json()],
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


def test_uncommitted_edits_are_included_automatically_and_secrets_are_not(tmp_path):
    import subprocess
    repo = make_repo(tmp_path)
    (repo / "src" / "app.py").write_text("print('edited but not committed')\n")
    (repo / ".env").write_text("TOKEN=abc\n")
    (repo / "src" / "fresh.py").write_text("y = 1\n")
    seen = {}

    def spy(prompt, cwd):
        seen["app"] = (Path(cwd) / "src" / "app.py").read_text()
        seen["fresh"] = (Path(cwd) / "src" / "fresh.py").exists()
        seen["env"] = (Path(cwd) / ".env").exists()
        return write_file("src/new.py")(prompt, cwd)

    orch, p = three(make_settings(), [order_json(), review_json()], [spy])
    r = orch.run("goal", repo)
    assert r.status == "PASSED"
    assert "edited but not committed" in seen["app"] and seen["fresh"] and not seen["env"]
    assert any("SNAPSHOT" in w for w in r.warnings)
    # the user's checkout, index and branch are untouched by the snapshot
    st = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert " M src/app.py" in st and "?? src/fresh.py" in st and "A " not in st.replace("?? ", "")
    assert (repo / "src" / "app.py").read_text() == "print('edited but not committed')\n"


def test_snapshot_can_be_turned_off(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "src" / "app.py").write_text("changed\n")
    seen = {}

    def spy(prompt, cwd):
        seen["app"] = (Path(cwd) / "src" / "app.py").read_text()
        return write_file("src/new.py")(prompt, cwd)

    orch, p = three(make_settings(include_uncommitted=False), [order_json(), review_json()], [spy])
    r = orch.run("goal", repo)
    assert seen["app"] == "print('hi')\n" and any("NOT part" in w for w in r.warnings)


# ---------------------------------------------------------------- up to three AIs per stage
def make3(settings, claude=(), codex=(), agy=(), **kw):
    provs = {"claude": ScriptedProvider("claude", list(claude)), "codex": ScriptedProvider("codex", list(codex)),
             "agy": ScriptedProvider("agy", list(agy))}
    provs["agy"].supports_write = False
    return Orchestrator(settings, provs, **kw), provs


def critic(verdict="CHANGE", concerns=("edge case: empty input",), extra=("handles empty input",)):
    return json.dumps({"verdict": verdict, "concerns": list(concerns), "extra_acceptance_criteria": list(extra)})


def test_plan_critics_add_criteria_but_never_widen_the_scope(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings()
    s.roles.update(planner2="codex", planner3="agy")
    widen = json.dumps({"verdict": "CHANGE", "concerns": ["c"], "extra_acceptance_criteria": ["x"], "allowed_paths": ["/"],
                        "max_files_changed": 999})
    orch, p = make3(s, claude=[order_json(), review_json()], codex=[critic(), write_file("src/new.py")], agy=[widen])
    r = orch.run("goal", repo)
    assert r.status == "PASSED"
    order = next(e["order"] for e in events(r) if e["event"] == "order")
    assert "handles empty input" in order["acceptance_criteria"] and "x" in order["acceptance_criteria"]
    assert order["allowed_paths"] == ["src/"] and order["max_files_changed"] == 5
    assert "edge case: empty input" in order["context"]
    assert [e["provider"] for e in events(r) if e["event"] == "plan_review"] == ["codex", "agy"]
    # the builder saw the merged criteria
    assert "handles empty input" in p["codex"].calls[1]["prompt"]


def test_a_plan_critic_that_fails_or_hits_its_quota_does_not_stop_the_run(tmp_path):
    from solomon.providers import QuotaExhausted
    repo = make_repo(tmp_path)
    s = make_settings()
    s.roles.update(planner2="agy", planner3="codex")
    orch, p = make3(s, claude=[order_json(), review_json()], codex=["not json", write_file("src/new.py")],
                    agy=[QuotaExhausted("usage limit reached")])
    r = orch.run("goal", repo)
    assert r.status == "PASSED"
    kinds = [e["event"] for e in events(r)]
    assert "helper_skipped" in kinds and any(e.get("verdict") == "UNPARSEABLE" for e in events(r) if e["event"] == "plan_review")


def test_three_reviewers_must_all_pass_and_stop_at_the_first_fail(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings(max_attempts=1)
    s.roles.update(reviewer2="agy", reviewer3="claude")  # claude twice is allowed: it is not the builder
    orch, p = make3(s, claude=[order_json(), review_json("PASS"), review_json("PASS")], codex=[write_file("src/new.py")],
                    agy=[review_json("FAIL", ["agy says no"])])
    r = orch.run("goal", repo)
    assert r.status == "FAILED"
    assert len(p["claude"].calls) == 2  # planner + reviewer; reviewer3 was never asked because reviewer2 failed
    assert [e["verdict"] for e in events(r) if e["event"] == "review_role"] == ["PASS", "FAIL"]


def test_all_three_reviewers_passing_passes(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings()
    s.roles.update(reviewer2="agy", reviewer3="claude")
    orch, p = make3(s, claude=[order_json(), review_json(), review_json()], codex=[write_file("src/new.py")], agy=[review_json()])
    r = orch.run("goal", repo)
    assert r.status == "PASSED" and len([e for e in events(r) if e["event"] == "review_role"]) == 3


def test_builders_take_turns_after_failures_and_advisors_help(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings(max_attempts=0, stagnation_limit=50)
    s.roles.update(builder2="claude", builder3="agy", reviewer2="agy")
    s.roles["reviewer"] = "claude"  # claude reviews attempts that codex built; when claude builds, agy must review
    advisor = "스펙을 먼저 읽고 빈 입력을 처리하세요"
    # attempt1 codex (tests fail), attempt2 codex (fails; 2nd consecutive failure -> hand over), attempt3 claude builds
    orch, p = make3(s,
                    claude=[order_json(), write_file("src/new.py")],
                    codex=[write_file("src/other.py", "x=1\n"), write_file("src/other.py", "x=2\n")],
                    agy=[advisor, review_json()])
    r = orch.run("goal", repo)
    assert r.status == "PASSED", r.summary
    picks = [(e["attempt"], e["provider"]) for e in events(r) if e["event"] == "builder_pick"]
    assert picks == [(1, "codex"), (2, "codex"), (3, "claude")]
    assert advisor in p["codex"].calls[0]["prompt"]  # the read-only third AI advised before the first attempt
    # when claude built, claude was skipped as its own reviewer and agy reviewed
    assert any(e["event"] == "review_skipped_self" and e["provider"] == "claude" for e in events(r))


def test_a_builder_failure_hands_over_to_the_next_builder(tmp_path):
    from solomon.providers import ProviderError
    repo = make_repo(tmp_path)
    s = make_settings()
    s.roles.update(builder2="claude", reviewer="agy")
    orch, p = make3(s, claude=[order_json(), write_file("src/new.py")], codex=[ProviderError("codex exited with 1: boom")],
                    agy=[review_json()])
    r = orch.run("goal", repo)
    assert r.status == "PASSED" and [e["provider"] for e in events(r) if e["event"] == "builder_pick"] == ["codex", "claude"]


def test_no_independent_reviewer_is_a_config_error_not_a_silent_self_review(tmp_path):
    s = make_settings()
    s.roles.update(builder2="claude")  # claude is the only reviewer, and a builder: when it builds nobody else reviews
    with pytest.raises(ConfigError):
        make3(s)


def test_roles_validation_and_toml(tmp_path):
    f = tmp_path / "s.toml"
    f.write_text('[roles]\nplanner2 = "agy"\nbuilder2 = "claude"\nreviewer3 = ""\n')
    s = load_settings(f)
    assert s.roles["planner2"] == "agy" and "reviewer3" not in s.roles
    f.write_text('[roles]\nplanner4 = "x"\n')
    with pytest.raises(ConfigError):
        load_settings(f)


# ---------------------------------------------------------------- plan B: usage limits never end the night
def _failover_orch(tmp_path, claude, codex, agy=None, **settings):
    from solomon.quota import QuotaBoard
    s = make_settings(auto_failover=True, **settings)
    ps = {"claude": ScriptedProvider("claude", claude), "codex": ScriptedProvider("codex", codex)}
    if agy is not None:
        ps["agy"] = ScriptedProvider("agy", agy)
        ps["agy"].supports_write = False
    sleeps = []
    return Orchestrator(s, ps, sleep=sleeps.append, quota_board=QuotaBoard()), ps, sleeps


def _events(r):
    return [json.loads(l) for l in (Path(r.run_dir) / "events.jsonl").read_text().splitlines()]


def test_when_the_reviewer_runs_out_the_next_ai_takes_the_same_call(tmp_path):
    from solomon.providers import QuotaExhausted
    repo = make_repo(tmp_path)
    s = make_settings(auto_failover=True)
    s.roles.update({"planner": "claude", "builder": "codex", "reviewer": "claude"})
    s.failover = {"reviewer": ["agy"], "planner": ["agy"]}
    from solomon.quota import QuotaBoard
    # claude plans, then is out of usage when asked to review: agy takes the review
    ps = {"claude": ScriptedProvider("claude", [order_json(), QuotaExhausted("usage limit reached")]),
          "codex": ScriptedProvider("codex", [write_file("src/new.py")]), "agy": ScriptedProvider("agy", [review_json()])}
    r = Orchestrator(s, ps, sleep=lambda x: None, quota_board=QuotaBoard()).run("add module", repo)
    assert r.status == "PASSED"
    fo = [e for e in _events(r) if e["event"] == "failover"]
    assert fo and fo[0]["from"] == "claude" and fo[0]["to"] == "agy" and fo[0]["role"] == "reviewer"


def test_when_the_builder_runs_out_the_other_writer_continues_the_same_attempt(tmp_path):
    from solomon.providers import QuotaExhausted
    from solomon.quota import QuotaBoard
    repo = make_repo(tmp_path)
    s = make_settings(auto_failover=True)
    s.roles.update({"planner": "agy", "builder": "claude", "reviewer": "agy"})
    agy = ScriptedProvider("agy", [order_json(), review_json()])
    agy.supports_write = False
    ps = {"claude": ScriptedProvider("claude", [QuotaExhausted("usage limit reached")]), "codex": ScriptedProvider("codex", [write_file("src/new.py")]), "agy": agy}
    r = Orchestrator(s, ps, sleep=lambda x: None, quota_board=QuotaBoard()).run("add module", repo)
    assert r.status == "PASSED" and r.attempts == 1  # no extra attempt was burnt
    fo = [e for e in _events(r) if e["event"] == "failover"]
    assert fo[0]["role"] == "builder" and fo[0]["from"] == "claude" and fo[0]["to"] == "codex"
    assert ps["codex"].calls[0]["write"] is True


def test_known_limit_is_avoided_from_the_start_of_the_next_run(tmp_path):
    from solomon.quota import QuotaBoard
    repo = make_repo(tmp_path)
    board = QuotaBoard()
    board.mark("claude", "usage limit reached", 1000.0, 120)
    s = make_settings(auto_failover=True)
    s.roles.update({"planner": "claude", "builder": "codex", "reviewer": "claude"})
    s.failover = {"reviewer": ["agy"], "planner": ["agy"]}
    ps = {"claude": ScriptedProvider("claude", []), "codex": ScriptedProvider("codex", [write_file("src/new.py")]),
          "agy": ScriptedProvider("agy", [order_json(), review_json()])}
    o = Orchestrator(s, ps, sleep=lambda x: None, clock=lambda: 1500.0, quota_board=board)
    r = o.run("add module", repo)
    assert r.status == "PASSED" and not ps["claude"].calls  # claude was never even asked
    fo = [e for e in _events(r) if e["event"] == "failover"]
    assert {e["role"] for e in fo} == {"planner", "reviewer"} and all(e["to"] == "agy" for e in fo)
    # a later run restores the configured roles once the limit has passed
    o._clock = lambda: 1000.0 + 121 * 60
    ps["claude"].steps = [order_json(), review_json()]
    ps["codex"].steps = [write_file("src/new.py")]
    r2 = o.run("add module", repo)
    assert r2.status == "PASSED" and ps["claude"].calls


def test_when_every_ai_is_out_it_waits_for_the_reset_and_carries_on(tmp_path):
    from solomon.providers import QuotaExhausted
    from solomon.quota import QuotaBoard
    repo = make_repo(tmp_path)
    s = make_settings(auto_failover=True)
    s.roles.update({"planner": "claude", "builder": "codex", "reviewer": "claude"})
    s.failover = {"planner": ["claude"], "reviewer": ["claude"]}  # nobody else is allowed to stand in
    ps = {"claude": ScriptedProvider("claude", [QuotaExhausted("usage limit reached"), order_json(), review_json()]),
          "codex": ScriptedProvider("codex", [write_file("src/new.py")])}
    sleeps = []
    r = Orchestrator(s, ps, sleep=sleeps.append, quota_board=QuotaBoard()).run("add module", repo)
    assert r.status == "PASSED" and sleeps and sleeps[0] >= 60
    assert any(e["event"] == "quota_wait" for e in _events(r))


def test_plan_b_can_be_switched_off_and_the_arbiter_never_fails_over(tmp_path):
    from solomon.providers import QuotaExhausted
    from solomon.quota import QuotaBoard
    repo = make_repo(tmp_path)
    s = make_settings(auto_failover=False)
    ps = {"claude": ScriptedProvider("claude", [QuotaExhausted("usage limit reached")]), "codex": ScriptedProvider("codex", [])}
    r = Orchestrator(s, ps, sleep=lambda x: None, quota_board=QuotaBoard()).run("add module", repo)
    assert r.status == "BLOCKED_QUOTA"
    o = Orchestrator(make_settings(auto_failover=True), ps, quota_board=QuotaBoard())
    assert o._candidates("arbiter", "claude") == []


def test_reset_time_in_the_message_is_used_and_the_board_forgets_after_it():
    from solomon.quota import QuotaBoard, parse_reset
    assert parse_reset("Claude AI usage limit reached|1900000000", 1000.0) == 1900000000.0
    assert parse_reset("usage limit reached", 1000.0) is None
    b = QuotaBoard()
    assert b.mark("claude", "limit", 1000.0, 60) == 4600.0
    assert b.exhausted("claude", 4599.0) and not b.exhausted("claude", 4601.0)
    b.clear("claude")
    assert not b.exhausted("claude", 1001.0)


def test_failover_is_visible_in_the_status_and_configurable_from_toml(tmp_path):
    from solomon import status
    from solomon.providers import QuotaExhausted
    from solomon.quota import QuotaBoard
    repo = make_repo(tmp_path)
    s = make_settings(auto_failover=True)
    s.roles.update({"planner": "agy", "builder": "claude", "reviewer": "agy"})
    agy = ScriptedProvider("agy", [order_json(), review_json()])
    agy.supports_write = False
    ps = {"claude": ScriptedProvider("claude", [QuotaExhausted("usage limit reached")]), "codex": ScriptedProvider("codex", [write_file("src/new.py")]), "agy": agy}
    r = Orchestrator(s, ps, sleep=lambda x: None, quota_board=QuotaBoard()).run("add module", repo)
    d = status.summarize_run(r.run_id, _events(r)).to_dict()
    assert d["failovers"][0]["to"] == "codex" and any("자동 전환" in t for t in d["timeline"])
    cfg = tmp_path / "s.toml"
    cfg.write_text('[failover]\nauto = false\ncooldown_min = 30\nbuilder = ["claude"]\n')
    loaded = load_settings(cfg)
    assert loaded.auto_failover is False and loaded.quota_cooldown_min == 30 and loaded.failover == {"builder": ["claude"]}


# ---------------------------------------------------------------- a failure that keeps coming back must end the run (37 attempts was a bug)
def test_signature_ignores_timings_paths_and_the_words_an_ai_writes():
    sig = Orchestrator._signature
    t1 = "Tests failed (exit 1):\n=== short test summary ===\nFAILED tests/test_a.py::test_x - AssertionError: 1.23s\nFAILED tests/test_b.py::test_y - boom\n2 failed in 4.1s"
    t2 = "Tests failed (exit 1):\nsome other header /tmp/xyz/run-9\nFAILED tests/test_b.py::test_y - different message\nFAILED tests/test_a.py::test_x\n2 failed in 99.9s"
    assert sig([t1]) == sig([t2])
    assert sig([t1]) != sig(["Tests failed (exit 1):\nFAILED tests/test_c.py::test_z - x"])
    assert sig(["Reviewer issues:\n- 경계값 처리가 없습니다"]) == sig(["Reviewer issues:\n- 완전히 다른 문장으로 쓴 지적입니다", "x"[:0] or "Reviewer issues:\n- 또 다른"])
    assert sig([t1, "Reviewer issues:\n- a"]) != sig([t1])


def test_endless_look_alike_failures_stop_the_run_after_the_allowed_restarts(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings(max_attempts=0, stagnation_limit=3, max_restarts=1)
    s.test_command = ["python3", "-c", "import random; print('FAILED tests/test_a.py::test_x - boom', random.random()); print('1 failed in', random.random(), 's'); raise SystemExit(1)"]
    n = 40
    ps = {"claude": ScriptedProvider("claude", [order_json()] + [review_json("FAIL", [f"지적 {i}번: 문장이 매번 다릅니다 {i * 7}"]) for i in range(n)]),
          "codex": ScriptedProvider("codex", [write_file("src/new.py", f"x = {i}\n") for i in range(n)])}
    r = Orchestrator(s, ps, sleep=lambda x: None).run("add module", repo)
    assert r.status == "FAILED" and "반복돼 중단" in r.summary
    assert r.attempts <= 3 * (1 + 1) + 1, r.attempts  # three look-alikes, one clean restart, three more -> stop


def test_mixed_failure_kinds_do_not_hide_a_repeating_one(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings(max_attempts=0, stagnation_limit=3, max_restarts=0)
    s.test_command = ["python3", "-c", "import sys, pathlib; print('FAILED tests/test_a.py::test_x - boom'); sys.exit(1)"]
    n = 30
    # every attempt fails the same test; the reviewer sometimes passes, sometimes fails: the recurring test failure must still be seen
    reviews = [review_json("PASS" if i % 2 else "FAIL", [] if i % 2 else [f"x{i}"]) for i in range(n)]
    ps = {"claude": ScriptedProvider("claude", [order_json()] + reviews), "codex": ScriptedProvider("codex", [write_file("src/new.py", f"x = {i}\n") for i in range(n)])}
    r = Orchestrator(s, ps, sleep=lambda x: None).run("add module", repo)
    assert r.status == "FAILED" and r.attempts <= 6, (r.status, r.attempts)


# ---------------------------------------------------------------- fewer pointless failures
def test_review_is_not_asked_while_the_tests_still_fail(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings(max_attempts=1)
    orch, p = three(s, [order_json()], [write_file("src/other.py", "x=1\n")])  # a review call would exhaust the script
    r = orch.run("goal", repo)
    assert r.status == "FAILED" and len(p["claude"].calls) == 1
    ev = [e for e in events(r) if e["event"] == "review"]
    assert ev and ev[0]["verdict"] == "SKIPPED"


def test_only_blockers_send_work_back_minor_remarks_become_notes(tmp_path):
    from solomon.schemas import ReviewResult
    r = ReviewResult.from_dict({"verdict": "FAIL", "issues": [{"severity": "minor", "text": "변수 이름을 바꾸면 좋겠습니다"}], "summary": "s"})
    assert r.verdict == "PASS" and r.issues == [] and r.notes == ["변수 이름을 바꾸면 좋겠습니다"]
    r = ReviewResult.from_dict({"verdict": "FAIL", "issues": [{"severity": "minor", "text": "m"}, {"severity": "blocker", "text": "경계값에서 죽습니다"}]})
    assert r.verdict == "FAIL" and r.issues == ["경계값에서 죽습니다"] and r.notes == ["m"]
    assert ReviewResult.from_dict({"verdict": "FAIL", "issues": ["old style string"]}).verdict == "FAIL"  # plain strings stay blockers
    assert ReviewResult.from_dict({"verdict": "FAIL", "issues": []}).verdict == "FAIL"  # FAIL with nothing said is still a FAIL
    repo = make_repo(tmp_path)
    orch, p = three(make_settings(), [order_json(), json.dumps({"verdict": "FAIL", "issues": [{"severity": "minor", "text": "스타일"}], "summary": "ok"})],
                    [write_file("src/new.py")])
    res = orch.run("goal", repo)
    assert res.status == "PASSED" and any(w.startswith("REVIEW_NOTES") and "스타일" in w for w in res.warnings)


def test_the_reviewer_prompt_asks_for_severity():
    from solomon import prompts
    from solomon.schemas import Order
    text = prompts.reviewer(Order("g", ["src/"]), "diff", "ok", [])
    assert '"severity": "blocker" | "minor"' in text and "must NOT cause FAIL" in text
