import json
import subprocess
from pathlib import Path

import pytest

from solomon import autofix
from solomon.orchestrator import Orchestrator

from helpers import ScriptedProvider, make_repo, make_settings, order_json, review_json, write_file


def cluster(i, severity="high", verified=True, agreement=2, file="src/app.py"):
    return {"id": i, "file": file, "line_start": 1, "line_end": 1, "severity": severity, "ais": ["claude", "codex"][:agreement],
            "agreement": agreement, "verified": verified,
            "members": [{"ai": "claude", "title": f"problem {i}", "severity": severity, "category": "bug", "problem": "bad",
                         "suggestion": "fix it", "quote": "print('hi')", "evidence": "verified" if verified else "quote_missing",
                         "line_start": 1, "line_end": 1}]}


def put_analysis(repo, clusters, name="20260101-000000-aaaa"):
    d = repo / ".solomon" / "analyses" / name
    d.mkdir(parents=True)
    (d / "analysis.json").write_text(json.dumps({"analysis_id": name, "clusters": clusters}))


def git(repo, *a):
    return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, check=True).stdout


def orch(settings, claude, codex):
    return Orchestrator(settings, {"claude": ScriptedProvider("claude", claude), "codex": ScriptedProvider("codex", codex)}), None


def test_select_filters_and_orders():
    a = {"clusters": [cluster(1, "low"), cluster(2, "high", verified=False), cluster(3, "medium", agreement=1),
                      cluster(4, "high"), cluster(5, "medium", file="")]}
    ids = [c["id"] for c in autofix.select_findings(a, "medium", 1, True, 5)]
    assert ids == [3, 4]
    assert [c["id"] for c in autofix.select_findings(a, "medium", 2)] == [4]
    assert [c["id"] for c in autofix.select_findings(a, "low", 1, False)] == [1, 2, 3, 4]
    assert len(autofix.select_findings(a, "low", 1, False, 2)) == 2


def test_chain_of_fixes_builds_on_one_branch_and_never_touches_the_checkout(tmp_path):
    repo = make_repo(tmp_path)
    put_analysis(repo, [cluster(1), cluster(2)])
    s = make_settings()
    # fix 2 can only pass if fix 1's file is already there: that proves the second run started from the first's result
    s.test_command = ["python3", "-c", "import pathlib,sys; sys.exit(0 if pathlib.Path('src/new.py').exists() else 1)"]
    o, _ = orch(s, [order_json(), review_json(), order_json(), review_json()],
                [write_file("src/new.py"), write_file("src/two.py")])
    head = git(repo, "rev-parse", "HEAD").strip()
    r = autofix.run_autofix(s, repo, orchestrator=o)
    assert r.status == "DONE" and [i.status for i in r.items] == ["PASSED", "PASSED"]
    tip = git(repo, "ls-tree", "-r", "--name-only", r.branch).split()
    assert "src/new.py" in tip and "src/two.py" in tip
    assert git(repo, "rev-parse", "HEAD").strip() == head
    assert not (repo / "src" / "new.py").exists()
    assert git(repo, "status", "--porcelain").strip() == ""
    assert git(repo, "log", "-1", "--format=%an", r.branch).strip() == "Solomon"
    patch = Path(r.patch_path).read_text()
    assert "src/new.py" in patch and "src/two.py" in patch
    assert "git -C" in Path(r.report_path).read_text()


def test_failures_do_not_end_the_night_but_three_in_a_row_do(tmp_path):
    repo = make_repo(tmp_path)
    put_analysis(repo, [cluster(i) for i in range(1, 6)])
    s = make_settings(max_attempts=1)
    fail = [order_json(), review_json("FAIL", ["no"])]
    o, _ = orch(s, fail * 3, [write_file("src/other.py")] * 3)
    r = autofix.run_autofix(s, repo, orchestrator=o)
    assert [i.status for i in r.items] == ["FAILED"] * 3 + ["SKIPPED"] * 2
    assert "연속 실패" in r.summary and not r.patch_path


def test_partial_success_keeps_the_good_fixes(tmp_path):
    repo = make_repo(tmp_path)
    put_analysis(repo, [cluster(1), cluster(2)])
    s = make_settings(max_attempts=1)
    o, _ = orch(s, [order_json(), review_json("FAIL", ["no"]), order_json(), review_json()],
                [write_file("src/other.py"), write_file("src/new.py")])
    r = autofix.run_autofix(s, repo, orchestrator=o)
    assert r.status == "PARTIAL" and [i.status for i in r.items] == ["FAILED", "PASSED"]
    assert "src/new.py" in git(repo, "ls-tree", "-r", "--name-only", r.branch)


def test_quota_stops_the_batch_without_going_on(tmp_path):
    from solomon.providers import QuotaExhausted
    repo = make_repo(tmp_path)
    put_analysis(repo, [cluster(1), cluster(2)])
    s = make_settings()
    o, _ = orch(s, [order_json()], [QuotaExhausted("usage limit reached")])
    r = autofix.run_autofix(s, repo, orchestrator=o)
    assert r.status == "BLOCKED_QUOTA" and [i.status for i in r.items] == ["BLOCKED_QUOTA", "SKIPPED"]


def test_time_budget_skips_the_rest(tmp_path):
    repo = make_repo(tmp_path)
    put_analysis(repo, [cluster(1), cluster(2)])
    s = make_settings()
    t = {"n": 0.0}

    def clock():
        t["n"] += 3600.0
        return t["n"]

    o, _ = orch(s, [], [])
    r = autofix.run_autofix(s, repo, orchestrator=o, budget_minutes=30, clock=clock)
    assert [i.status for i in r.items] == ["SKIPPED", "SKIPPED"] and "시간 예산" in r.summary


def test_refuses_without_tests_and_without_analysis(tmp_path):
    repo = make_repo(tmp_path)
    s = make_settings()
    with pytest.raises(autofix.AutofixError, match="분석"):
        autofix.run_autofix(s, repo)
    put_analysis(repo, [cluster(1)])
    s.test_command = None
    with pytest.raises(autofix.AutofixError, match="테스트"):
        autofix.run_autofix(s, repo)


def test_nothing_to_fix(tmp_path):
    repo = make_repo(tmp_path)
    put_analysis(repo, [cluster(1, "low")])
    r = autofix.run_autofix(make_settings(), repo)
    assert r.status == "NOTHING" and r.items == []


def test_goal_carries_guardrails_and_quote():
    g = autofix.build_goal(cluster(1))
    assert "ONLY this problem" in g and "print('hi')" in g and "secrets" in g


def test_cli_autofix_errors_cleanly_without_analysis(tmp_path, capsys):
    from solomon import cli
    repo = make_repo(tmp_path)
    assert cli.main(["autofix", "--project", str(repo), "--test-command", "true"]) == 2
    assert "분석" in capsys.readouterr().err


def test_cli_autofix_passes_options_through(tmp_path, monkeypatch, capsys):
    from solomon import cli
    seen = {}

    def fake(settings, project, analysis_id=None, **kw):
        seen.update(kw, analysis=analysis_id)
        return autofix.BatchResult("b", "NOTHING", "none", report_path="r.md")

    monkeypatch.setattr(autofix, "run_autofix", fake)
    repo = make_repo(tmp_path)
    assert cli.main(["autofix", "--project", str(repo), "--min-severity", "high", "--max-fixes", "2", "--budget-minutes", "60"]) == 0
    assert seen["min_severity"] == "high" and seen["max_fixes"] == 2 and seen["budget_minutes"] == 60.0 and seen["verified_only"] is True


# ------------------------------------------------------------------ dashboard
from test_dashboard import Env, sleeper  # noqa: E402


@pytest.fixture
def env(tmp_path):
    sleeper.calls.clear()
    e = Env(tmp_path)
    yield e
    e.close()


def test_dashboard_starts_an_autofix_job_with_checked_options(env):
    st, body, _ = env.call("POST", "/api/autofix", {"project": env.project, "min_severity": "high", "max_fixes": 3,
                                                    "budget_hours": 2})
    assert st == 200
    a = sleeper.calls[-1]
    assert a[3] == "autofix" and a[a.index("--min-severity") + 1] == "high" and a[a.index("--budget-minutes") + 1] == "120"
    assert "--test-command" in a
    assert env.state()[1]["job"]["kind"] == "autofix"


@pytest.mark.parametrize("opts", [{"min_severity": "x"}, {"max_fixes": 101}, {"budget_hours": 0},
                                  {"budget_hours": "abc"}, {"roles": {"builder": "antigravity"}}])
def test_dashboard_rejects_bad_autofix_options(env, opts):
    sleeper.calls.clear()
    st, body, _ = env.call("POST", "/api/autofix", {"project": env.project, **opts})
    assert st == 400 and not sleeper.calls


def test_dashboard_autofix_needs_a_test_command(env):
    env.call("POST", "/api/test-command", {"project": env.project, "command": "없음"})
    sleeper.calls.clear()
    st, body, _ = env.call("POST", "/api/autofix", {"project": env.project})
    assert st == 400 and "테스트" in body["error"] and not sleeper.calls


def test_state_lists_batches_and_apply_batch_applies_once(env):
    bdir = env.repo / ".solomon" / "batches" / "20260101-000000-abcd"
    bdir.mkdir(parents=True)
    (bdir / "batch.json").write_text(json.dumps({"batch_id": "x", "status": "DONE", "summary": "1건 중 1건 수정", "items": []}))
    (bdir / "combined.patch").write_text(
        "diff --git a/src/new.py b/src/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1 @@\n+x = 1\n")
    b = env.state()[1]["batches"][0]
    assert b["batch_id"] == "20260101-000000-abcd" and b["has_patch"] and not b["applied"]
    st, body, _ = env.call("POST", "/api/apply-batch", {"project": env.project, "batch_id": "20260101-000000-abcd"})
    assert st == 200 and (env.repo / "src" / "new.py").read_text() == "x = 1\n"
    assert env.call("POST", "/api/apply-batch", {"project": env.project, "batch_id": "20260101-000000-abcd"})[0] == 409
    assert env.call("POST", "/api/apply-batch", {"project": env.project, "batch_id": "../x"})[0] == 400


def test_running_batch_is_rebuilt_from_events(tmp_path):
    bdir = tmp_path / ".solomon" / "batches" / "20260101-000000-abcd"
    bdir.mkdir(parents=True)
    lines = [{"event": "batch_start", "items": [{"title": "a", "status": "PENDING"}, {"title": "b", "status": "PENDING"}], "branch": "br"},
             {"event": "item_end", "n": 1, "status": "PASSED", "attempts": 2, "summary": "ok"}]
    (bdir / "events.jsonl").write_text("\n".join(json.dumps(l) for l in lines))
    row = autofix.list_batches(tmp_path, ".solomon")[0]
    assert row["status"] == "RUNNING" and not row["finished"] and [i["status"] for i in row["items"]] == ["PASSED", "PENDING"]


def test_stop_script_knows_autofix_jobs():
    assert "autofix" in (Path(__file__).parent.parent / "scripts" / "stop.sh").read_text()


# ---------------------------------------------------- fix everything in one go: ids, grouping, no cap
def test_ids_select_exactly_the_ticked_findings_ignoring_the_filters():
    a = {"clusters": [cluster(1, "low"), cluster(2, "high", verified=False), cluster(3, "medium"), cluster(4, "high", file="")]}
    assert [c["id"] for c in autofix.select_findings(a, ids=[1, 2, 4])] == [1, 2]  # no file -> cannot be fixed; filters not applied
    assert [c["id"] for c in autofix.select_findings(a, max_fixes=0, min_severity="low", verified_only=False)] == [1, 2, 3]  # 0 = no cap


def test_group_by_file_merges_findings_of_one_file_up_to_the_limit():
    cs = [cluster(i, file="a.py") for i in range(1, 9)] + [cluster(20, file="b.py")]
    groups = autofix.group_by_file(cs)
    assert [len(g) for g in groups] == [autofix.GROUP_MAX, autofix.GROUP_MAX, 8 - 2 * autofix.GROUP_MAX, 1] and autofix.GROUP_MAX == 3
    goal = autofix.build_group_goal(groups[0])
    assert goal.count("quote:") == autofix.GROUP_MAX and "all of them" in goal and "secrets" in goal
    assert autofix.build_group_goal([cs[0]]) == autofix.build_goal(cs[0])  # a single one is the normal goal


def test_one_run_fixes_all_findings_of_a_file_and_the_report_says_so(tmp_path):
    repo = make_repo(tmp_path)
    put_analysis(repo, [cluster(1, file="src/app.py"), cluster(2, file="src/app.py"), cluster(3, file="src/app.py")])
    s = make_settings()
    o = Orchestrator(s, {"claude": ScriptedProvider("claude", [order_json(), review_json()]),
                         "codex": ScriptedProvider("codex", [write_file("src/new.py")])})
    r = autofix.run_autofix(s, repo, orchestrator=o, group=True, max_fixes=0, ids=[1, 2, 3])
    assert r.status == "DONE" and len(r.items) == 1 and r.items[0].clusters == [1, 2, 3]
    prompt = o.providers["codex"].calls[0]["prompt"]
    assert "problem 1" in prompt and "problem 2" in prompt and "problem 3" in prompt  # one plan, one build, one review for all three
    assert len(o.providers["claude"].calls) == 2
    assert "묶음" in r.items[0].title


def test_cli_ids_group_and_no_cap_reach_the_engine(tmp_path, monkeypatch):
    from solomon import cli
    seen = {}

    def fake(settings, project, analysis_id=None, **kw):
        seen.update(kw)
        return autofix.BatchResult("b", "NOTHING", "none", report_path="r.md")

    monkeypatch.setattr(autofix, "run_autofix", fake)
    repo = make_repo(tmp_path)
    assert cli.main(["autofix", "--project", str(repo), "--ids", "1, 3,7", "--group-by-file", "--max-fixes", "0"]) == 0
    assert seen["ids"] == [1, 3, 7] and seen["group"] is True and seen["max_fixes"] == 0


def test_dashboard_autofix_accepts_ticked_ids_group_and_all(env):
    st, _, _ = env.call("POST", "/api/autofix", {"project": env.project, "ids": [1, 4, 9], "group": True, "max_fixes": 0,
                                                 "analysis_id": "20260101-000000-aaaa"})
    assert st == 200
    a = sleeper.calls[-1]
    assert a[a.index("--ids") + 1] == "1,4,9" and "--group-by-file" in a and a[a.index("--max-fixes") + 1] == "0"
    assert a[a.index("--analysis") + 1] == "20260101-000000-aaaa"
    assert env.state()[1]["job"]["goal"].startswith("선택한 3건")


@pytest.mark.parametrize("opts", [{"ids": []}, {"ids": ["1"]}, {"ids": [0]}, {"ids": [True]}, {"ids": "1,2"}, {"ids": list(range(1, 400))},
                                  {"analysis_id": "../x"}, {"max_fixes": 101}, {"max_fixes": -1}])
def test_dashboard_rejects_bad_ticked_ids(env, opts):
    sleeper.calls.clear()
    st, _, _ = env.call("POST", "/api/autofix", {"project": env.project, **opts})
    assert st == 400 and not sleeper.calls


def test_batch_5w1h_lists_every_finding_of_a_grouped_item(env):
    put_analysis(env.repo, [cluster(1, file="src/app.py"), cluster(2, file="src/app.py")])
    s = make_settings()
    o = Orchestrator(s, {"claude": ScriptedProvider("claude", [order_json(), review_json()]),
                         "codex": ScriptedProvider("codex", [write_file("src/new.py")])})
    b = autofix.run_autofix(s, env.repo, orchestrator=o, group=True, ids=[1, 2])
    from urllib.parse import quote
    d = env.call("GET", f"/api/describe?project={quote(env.project)}&key=batch:{b.batch_id}")[1]
    why = d["sections"]["why"]["lines"]
    assert any("problem 1" in l for l in why) and any("problem 2" in l for l in why)


def test_a_bundle_that_fails_is_retried_one_problem_at_a_time(tmp_path):
    repo = make_repo(tmp_path)
    put_analysis(repo, [cluster(1, file="src/app.py"), cluster(2, file="src/app.py")])
    s = make_settings(max_attempts=1)  # the bundle gets one attempt and fails (the tests need src/new.py)
    # bundle: plan + build that fails; then problem 1 alone: plan + build + review passes; problem 2 alone: plan + build + review passes
    claude = [order_json(), order_json(), review_json(), order_json(), review_json()]
    codex = [write_file("src/other.py", "x=1\n"), write_file("src/new.py"), write_file("src/new2.py") ]
    s.test_command = ["python3", "-c", "import pathlib,sys; sys.exit(0 if pathlib.Path('src/new.py').exists() else 1)"]
    o = Orchestrator(s, {"claude": ScriptedProvider("claude", claude), "codex": ScriptedProvider("codex", codex)})
    r = autofix.run_autofix(s, repo, orchestrator=o, group=True, max_fixes=0, ids=[1, 2])
    statuses = [i.status for i in r.items]
    assert statuses[0] == "FAILED" and "하나씩 따로" in r.items[0].summary and len(r.items) == 3
    assert statuses[1] == "PASSED"  # the first problem alone succeeds; the batch carries on instead of giving up


def test_a_batch_starts_from_the_folder_as_it_is_on_disk_so_its_patch_still_applies(tmp_path):
    from solomon import manage
    repo = make_repo(tmp_path)
    (repo / "src" / "app.py").write_text("print('edited by the user, not committed')\n")  # an uncommitted edit of the very file the fix touches
    put_analysis(repo, [cluster(1, file="src/app.py")])
    s = make_settings(include_uncommitted=True)

    def edit(prompt, cwd):  # the builder changes the same line again and adds the file the tests need
        (Path(cwd) / "src" / "app.py").write_text("print('fixed')\n")
        (Path(cwd) / "src" / "new.py").write_text("x = 1\n")
        return "done"

    o = Orchestrator(s, {"claude": ScriptedProvider("claude", [order_json(allowed=("src/",)), review_json()]), "codex": ScriptedProvider("codex", [edit])})
    r = autofix.run_autofix(s, repo, orchestrator=o, ids=[1])
    assert r.status == "DONE" and "커밋 안 된 변경" in r.summary
    ok, why = manage._check(repo, Path(r.patch_path))
    assert ok, why  # the finished patch applies onto the edited folder (a HEAD-based batch could not)



def test_conflict_reason_lists_every_conflicting_file(tmp_path):
    from solomon import manage
    repo = make_repo(tmp_path)
    (repo / "src" / "b.py").write_text("one\ntwo\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "b"], cwd=repo, check=True)
    (repo / "src" / "app.py").write_text("print('A')\n")
    (repo / "src" / "b.py").write_text("one\nTWO\n")
    patch = subprocess.run(["git", "diff"], cwd=repo, capture_output=True, text=True, check=True).stdout
    (repo / "src" / "app.py").write_text("print('somebody else')\n")  # the folder moved on after the patch was made
    (repo / "src" / "b.py").write_text("one\nelse\n")
    pf = tmp_path / "x.patch"
    pf.write_text(patch)
    ok, why = manage._check(repo, pf)
    assert not ok and "2개" in why and "src/app.py" in why and "src/b.py" in why
