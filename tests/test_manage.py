import json
import subprocess
from urllib.parse import quote

import pytest

from solomon import autofix, cli
from solomon.orchestrator import Orchestrator

from helpers import ScriptedProvider, make_settings, order_json, review_json, write_file
from test_dashboard import Env, sleeper
from test_autofix import cluster, put_analysis


@pytest.fixture
def env(tmp_path):
    sleeper.calls.clear()
    e = Env(tmp_path)
    yield e
    e.close()


def git(repo, *a):
    return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, check=True).stdout


def inbox(env):
    st, body, _ = env.call("GET", "/api/inbox?project=" + quote(env.project))
    assert st == 200, body
    return body


def act(env, key, action, message="", expect=200):
    st, body, _ = env.call("POST", "/api/inbox/action", {"project": env.project, "key": key, "action": action, "message": message})
    assert st == expect, body
    return body


def make_batch(env):
    put_analysis(env.repo, [cluster(1), cluster(2)])
    s = make_settings()
    o = Orchestrator(s, {"claude": ScriptedProvider("claude", [order_json(), review_json(), order_json(), review_json()]),
                         "codex": ScriptedProvider("codex", [write_file("src/new.py"), write_file("src/two.py")])})
    return autofix.run_autofix(s, env.repo, orchestrator=o)


def test_run_goes_pending_applied_committed_and_only_its_own_files_are_committed(env):
    res = env.finish_a_run()
    key = f"run:{res.run_id}"
    (env.repo / "README.md").write_text("# someone else's edit\n")  # unrelated work in the folder (like the bot's own files)
    e = next(x for x in inbox(env)["entries"] if x["key"] == key)
    assert e["state"] == "pending" and e["applicable"] is True and e["files"] == ["src/new.py"]

    act(env, key, "apply")
    assert (env.repo / "src" / "new.py").exists()
    act(env, key, "apply", expect=409)  # twice
    act(env, key, "commit", message="", expect=400)  # needs a message
    out = act(env, key, "commit", message="add new module via solomon")
    assert out["sha"]
    assert "add new module via solomon" in git(env.repo, "log", "-1", "--format=%s")
    assert git(env.repo, "show", "--name-only", "--format=", "HEAD").split() == ["src/new.py"]
    assert git(env.repo, "status", "--porcelain").strip() == "M README.md"  # the other edit was left alone
    e = next(x for x in inbox(env)["entries"] if x["key"] == key)
    assert e["state"] == "committed" and e["commit"] == out["sha"]
    act(env, key, "revert", expect=409)  # committed work is not "reverted" behind git's back


def test_revert_and_discard(env):
    a, b = env.finish_a_run(), None
    key = f"run:{a.run_id}"
    act(env, key, "apply")
    act(env, key, "revert")
    assert not (env.repo / "src" / "new.py").exists()
    assert next(x for x in inbox(env)["entries"] if x["key"] == key)["state"] == "pending"
    act(env, key, "discard")
    assert next(x for x in inbox(env)["entries"] if x["key"] == key)["state"] == "discarded"
    act(env, key, "apply", expect=409)


def test_applied_batch_can_be_reverted_and_discarded_if_the_patch_still_matches(env):
    batch = make_batch(env)
    key = f"batch:{batch.batch_id}"
    act(env, key, "apply")
    changed = env.repo / "src" / "new.py"
    applied = changed.read_bytes()
    edited = applied + b"\n# user edit\n"
    changed.write_bytes(edited)
    act(env, key, "revert_discard", expect=409)
    assert changed.read_bytes() == edited
    assert next(x for x in inbox(env)["entries"] if x["key"] == key)["state"] == "applied"

    changed.write_bytes(applied)
    act(env, key, "revert_discard")
    assert not changed.exists()
    assert next(x for x in inbox(env)["entries"] if x["key"] == key)["state"] == "discarded"
    act(env, key, "restore")
    assert next(x for x in inbox(env)["entries"] if x["key"] == key)["state"] == "pending"


def test_cannot_apply_while_a_job_runs_in_the_project(env):
    res = env.finish_a_run()
    env.call("POST", "/api/analyze", {"project": env.project})
    act(env, f"run:{res.run_id}", "apply", expect=409)


def test_old_applied_marker_is_understood_and_a_hand_committed_run_is_detected(env):
    res = env.finish_a_run()
    key = f"run:{res.run_id}"
    st, _, _ = env.call("POST", "/api/apply", {"project": env.project, "run_id": res.run_id})  # the old endpoint
    assert st == 200
    assert next(x for x in inbox(env)["entries"] if x["key"] == key)["state"] == "applied"
    git(env.repo, "add", "-A")
    git(env.repo, "commit", "-q", "-m", "by hand")
    e = next(x for x in inbox(env)["entries"] if x["key"] == key)
    assert e["state"] == "committed" and "직접 커밋" in e["note"]


def test_batch_items_whole_batch_and_discard(env):
    b = make_batch(env)
    assert b.status == "DONE"
    ents = inbox(env)["entries"]
    batch = next(x for x in ents if x["kind"] == "batch")
    assert [i["state"] for i in batch["items"]] == ["pending", "pending"] and all(i["commit"] for i in batch["items"])
    first, second = batch["items"]
    act(env, first["key"], "apply")
    assert (env.repo / "src" / "new.py").exists() and not (env.repo / "src" / "two.py").exists()
    act(env, batch["key"], "apply", expect=409)  # part of it is already in
    act(env, second["key"], "apply")
    act(env, first["key"], "commit", message="fix 1")
    act(env, second["key"], "commit", message="fix 2")
    assert git(env.repo, "log", "--format=%s", "-2").split("\n")[:2] == ["fix 2", "fix 1"]


def test_discard_is_not_destructive_and_can_be_restored(env):
    b = make_batch(env)
    key = f"batch:{b.batch_id}"
    act(env, key, "discard")
    assert b.branch in git(env.repo, "branch", "--list")  # nothing deleted
    ent = next(x for x in inbox(env)["entries"] if x["kind"] == "batch")
    assert ent["state"] == "discarded" and all(i["state"] == "discarded" for i in ent["items"])
    act(env, key, "apply", expect=409)
    act(env, key, "restore")
    ent = next(x for x in inbox(env)["entries"] if x["kind"] == "batch")
    assert ent["state"] == "pending" and [i["state"] for i in ent["items"]] == ["pending", "pending"]
    act(env, key, "restore", expect=409)  # not discarded any more
    act(env, key, "apply")
    assert (env.repo / "src" / "two.py").exists()


def test_restore_fails_cleanly_when_the_batch_branch_is_gone(env):
    b = make_batch(env)
    key = f"batch:{b.batch_id}"
    act(env, key, "discard")
    git(env.repo, "branch", "-D", b.branch)
    assert "브랜치가 없어" in act(env, key, "restore", expect=409)["error"]


def test_a_commit_made_here_can_be_undone_and_the_item_is_pending_again(env):
    res = env.finish_a_run()
    key = f"run:{res.run_id}"
    act(env, key, "apply")
    act(env, key, "commit", message="add module")
    assert (env.repo / "src" / "new.py").exists()
    out = act(env, key, "revert_commit")
    assert "취소" in out["message"] and not (env.repo / "src" / "new.py").exists()
    assert git(env.repo, "log", "-1", "--format=%s").startswith('Revert "add module"')
    assert next(x for x in inbox(env)["entries"] if x["key"] == key)["state"] == "pending"
    act(env, key, "apply")  # and it can be applied again
    assert (env.repo / "src" / "new.py").exists()


def test_undoing_a_commit_is_refused_for_hand_made_commits(env):
    res = env.finish_a_run()
    key = f"run:{res.run_id}"
    env.call("POST", "/api/apply", {"project": env.project, "run_id": res.run_id})
    git(env.repo, "add", "-A")
    git(env.repo, "commit", "-q", "-m", "by hand")
    assert "직접 커밋" in act(env, key, "revert_commit", expect=409)["error"]


def test_undoing_a_commit_is_refused_when_the_file_changed_afterwards_and_nothing_breaks(env):
    res = env.finish_a_run()
    key = f"run:{res.run_id}"
    act(env, key, "apply")
    act(env, key, "commit", message="add module")
    (env.repo / "src" / "new.py").write_text("x = 'later edit'\n")
    git(env.repo, "commit", "-q", "-am", "later edit")
    head = git(env.repo, "rev-parse", "HEAD").strip()
    assert "취소하지 못했습니다" in act(env, key, "revert_commit", expect=409)["error"]
    assert git(env.repo, "rev-parse", "HEAD").strip() == head and git(env.repo, "status", "--porcelain").strip() == ""
    assert next(x for x in inbox(env)["entries"] if x["key"] == key)["state"] == "committed"


def test_process_and_assessment_for_a_passed_run(env):
    res = env.finish_a_run()
    d = env.call("GET", f"/api/describe?project={quote(env.project)}&key=run:{res.run_id}")[1]
    steps = [s["name"] for s in d["process"]["attempts"][0]["steps"]]
    assert steps[:4] == ["구현", "바뀐 파일", "범위 검사", "테스트"] and any(n.startswith("검수 ·") for n in steps)
    assert [s["name"] for s in d["process"]["prelude"]][:2] == ["계획", "작업 지시서"]
    assert d["live"] is False
    a = d["assessment"]
    assert a["recommendation"] == "recommend" and {c["label"] for c in a["checks"]} >= {"범위 검사", "테스트", "변경 규모", "작성자와 검수자"}
    # an unrelated edit in the same file makes the same item a 'caution'
    (env.repo / "src" / "new.py").write_text("conflict\n")
    a2 = env.call("GET", f"/api/describe?project={quote(env.project)}&key=run:{res.run_id}")[1]["assessment"]
    assert a2["recommendation"] == "block" and any(c["label"] == "적용 가능 여부" for c in a2["checks"])


def test_a_failed_attempt_shows_why_in_the_process(env):
    s = make_settings(max_attempts=1)
    o = Orchestrator(s, {"claude": ScriptedProvider("claude", [order_json(), review_json("FAIL", ["missing test"])]),
                         "codex": ScriptedProvider("codex", [write_file("src/new.py")])})
    s.test_command = ["python3", "-c", "raise SystemExit(0)"]  # the tests pass, so the reviewer is asked and rejects
    r = o.run("goal", env.repo)
    key = f"run:{r.run_id}"
    d = env.call("GET", f"/api/describe?project={quote(env.project)}&key={key}")[1]
    names = {s["name"]: s for s in d["process"]["attempts"][0]["steps"]}
    assert names["테스트"]["status"] == "ok"
    rev = next(v for k, v in names.items() if k.startswith("검수 ·"))
    assert rev["status"] == "fail" and "missing test" in rev["lines"][0]
    assert d["assessment"]["recommendation"] == "block"


def test_whole_batch_applies_in_one_click(env):
    b = make_batch(env)
    act(env, f"batch:{b.batch_id}", "apply")
    assert (env.repo / "src" / "new.py").exists() and (env.repo / "src" / "two.py").exists()


def test_5w1h_for_run_batch_item_and_analysis(env):
    res = env.finish_a_run()
    d = env.call("GET", f"/api/describe?project={quote(env.project)}&key=run:{res.run_id}")[1]
    assert list(d["sections"]) == ["who", "what", "when", "where", "why", "how"]
    flat = json.dumps(d, ensure_ascii=False)
    assert "add new module" in flat and "src/new.py" in flat and "claude" in flat and "codex" in flat
    assert any(r[0] == "테스트" for r in d["sections"]["how"]["rows"])
    assert d["status"] == "PASSED"

    b = make_batch(env)
    bd = env.call("GET", f"/api/describe?project={quote(env.project)}&key=batch:{b.batch_id}")[1]
    flat = json.dumps(bd, ensure_ascii=False)
    assert "problem 1" in flat and "Solomon" in flat and b.branch in flat
    assert any("근거 확인됨" in l for l in bd["sections"]["why"]["lines"])  # why: the analysis finding behind each fix
    sha = b.items[0].commit[:10]
    idd = env.call("GET", f"/api/describe?project={quote(env.project)}&key=item:{b.batch_id}:{sha}")[1]
    assert idd["kind"] == "run" and any("밤샘 수정 항목" in r[0] for r in idd["sections"]["why"]["rows"])

    (env.repo / ".solomon" / "analyses" / "20260101-000000-aaaa" / "events.jsonl").write_text(
        json.dumps({"ts": "2026-01-01T00:00:00", "event": "analysis_start", "ais": ["claude"], "focus": "x"}) + "\n")
    ad = env.call("GET", f"/api/describe?project={quote(env.project)}&key=analysis:20260101-000000-aaaa")[1]
    assert ad["kind"] == "analysis"


def test_analysis_history_is_available_in_a_project_without_git(env, tmp_path):
    folder = tmp_path / "plain-folder"
    folder.mkdir()
    put_analysis(folder, [cluster(1)])
    env.app.add_project(str(folder))

    status, data, _ = env.call("GET", "/api/inbox?project=" + quote(str(folder.resolve())))
    assert status == 200
    assert data["not_git"] and data["entries"] == []
    assert [item["key"] for item in data["history"]] == ["analysis:20260101-000000-aaaa"]


@pytest.mark.parametrize("key", ["run:../../etc", "batch:x", "item:20260101-000000-abcd:zz", "bogus", "run:20261118-234722-c7c18a;rm"])
def test_bad_keys_are_rejected(env, key):
    assert env.call("GET", f"/api/describe?project={quote(env.project)}&key={quote(key)}")[0] in (400, 404)
    assert env.call("GET", f"/api/diff?project={quote(env.project)}&key={quote(key)}")[0] in (400, 404)
    assert env.call("POST", "/api/inbox/action", {"project": env.project, "key": key, "action": "apply"})[0] in (400, 404)


def test_diff_and_history_and_token(env):
    res = env.finish_a_run()
    st, d, _ = env.call("GET", f"/api/diff?project={quote(env.project)}&key=run:{res.run_id}")
    assert st == 200 and "+x = 1" in d["text"] and d["files"] == ["src/new.py"]
    hist = inbox(env)["history"]
    assert any(h["key"] == f"run:{res.run_id}" and h["status_ko"] == "통과" for h in hist)
    assert env.call("GET", f"/api/inbox?project={quote(env.project)}", token=None)[0] == 401
    assert env.call("GET", f"/api/describe?project={quote(env.project)}&key=run:{res.run_id}", token=None)[0] == 401
    assert env.call("POST", "/api/inbox/action", {"project": "/nope", "key": "x", "action": "apply"})[0] == 400


def test_unknown_action_and_tests_job(env):
    res = env.finish_a_run()
    act(env, f"run:{res.run_id}", "explode", expect=400)
    st, body, _ = env.call("POST", "/api/tests", {"project": env.project})
    assert st == 200 and sleeper.calls[-1][3] == "runtests"
    assert env.state()[1]["job"]["kind"] == "tests"


def test_runtests_cli_reports_pass_and_fail(tmp_path, capsys):
    (tmp_path / "ok.py").write_text("print('hi')\n")
    assert cli.main(["runtests", "--project", str(tmp_path), "--test-command", "python3 ok.py"]) == 0
    assert "결과: 통과" in capsys.readouterr().out
    assert cli.main(["runtests", "--project", str(tmp_path), "--test-command", "python3 missing.py"]) == 1
    assert "결과: 실패" in capsys.readouterr().out
