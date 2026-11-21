import json
from pathlib import Path
from urllib.parse import quote

import pytest

from solomon import cli, report as R
from solomon.providers.base import ProviderError, QuotaExhausted

from helpers import ScriptedProvider, make_repo, make_settings
from test_dashboard import Env, sleeper

DOC = "# 제목 {n}\n## 한 줄 결론\n결론 {n}\n## 구현 방안\n" + "1. 단계 설명이 충분히 길어야 보고서로 인정됩니다. " * 6 + "\n## 위험과 한계\n위험 {n}\n"


def doc(n):
    return DOC.format(n=n)


def merged():
    return "# 통합 보고서\n## 한 줄 결론\n합쳤습니다\n## 구현 방안\n" + "단계 " * 80 + "\n## AI 간 의견 차이\n| 주제 | AI별 의견 | 판단 |\n|---|---|---|\n| a | b | c |\n"


def providers(**steps):
    out = {}
    for name, st in steps.items():
        p = ScriptedProvider(name, list(st))
        p.supports_write = False
        out[name] = p
    return out


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


def run(repo, ps, ais=None, question="유튜브 자막으로 ena 봇 모델 육성 방법 보고서와 구현 방안", ctx=False):
    return R.run_report(make_settings(), repo, question, ais or list(ps), use_project_context=ctx, providers=ps)


def test_independent_drafts_are_merged_into_one_report_and_everything_is_kept(repo):
    ps = providers(claude=[doc(1), merged()], codex=[doc(2)], antigravity=[doc(3)])
    res = run(repo, ps)
    assert res["status"] == "DONE" and res["synthesizer"] == "claude" and res["title"] == "통합 보고서"
    d = Path(res["report_path"]).parent
    assert (d / "report.md").read_text().startswith("# 통합 보고서") and {f.name for f in d.glob("draft-*.md")} == {"draft-claude.md", "draft-codex.md", "draft-antigravity.md"}
    merge_prompt = ps["claude"].calls[1]["prompt"]  # the second call of the claude provider is the merge
    assert all(f"결론 {n}" in merge_prompt for n in (1, 2, 3)) and "AI 간 의견 차이" in merge_prompt and "단독 주장" in merge_prompt
    for p in ps.values():  # read-only, and each draft prompt carries the question and the report structure
        assert p.calls[0]["write"] is False and "유튜브 자막으로 ena 봇" in p.calls[0]["prompt"] and "## 한 줄 결론" in p.calls[0]["prompt"]
    assert not any(f"결론 {n}" in ps["codex"].calls[0]["prompt"] for n in (1, 3))  # independent: nobody sees the others while drafting
    ev = [json.loads(l)["event"] for l in (d / "events.jsonl").read_text().splitlines()]
    assert ev[0] == "report_start" and ev[-1] == "report_end" and "merge_start" in ev and ev.count("ai_end") == 3


def test_a_failed_ai_does_not_stop_the_report_and_a_single_draft_is_flagged(repo):
    ps = providers(claude=[doc(1)], codex=[QuotaExhausted("usage limit reached")], antigravity=[ProviderError("boom")])
    res = run(repo, ps)
    assert res["status"] == "DONE" and res["ais"]["codex"]["state"] == "quota" and res["ais"]["antigravity"]["state"] == "failed"
    md = Path(res["report_path"]).read_text()
    assert "교차 검증이 되지 않았습니다" in md and md.startswith("# 제목 1")
    assert "초안 1개" in res["summary"]


def test_when_the_merger_fails_the_longest_draft_is_shown_with_a_note(repo):
    ps = providers(claude=[doc(1), ProviderError("down")], codex=[doc(2) + "추가 내용 " * 100, ProviderError("down")])
    res = run(repo, ps)
    assert res["status"] == "DONE" and res["synthesizer"] == "codex"
    assert "통합을 맡을 AI가 응답하지 못해" in Path(res["report_path"]).read_text()


def test_nothing_usable_is_a_clear_failure(repo):
    ps = providers(claude=["짧은 답"], codex=[ProviderError("x")])
    res = run(repo, ps)
    assert res["status"] == "FAILED" and "어떤 AI도 보고서를 쓰지 못했습니다" in res["summary"] and "report_path" not in res


def test_chatty_wrappers_around_the_markdown_are_removed():
    assert R._clean("```markdown\n# 제목\n본문\n```").startswith("# 제목")
    assert R._clean("네, 작성하겠습니다.\n# 제목\n본문").startswith("# 제목")
    assert R._clean("# 이미 제목\n본문") == "# 이미 제목\n본문"


def test_validation(repo):
    with pytest.raises(R.ReportError):
        run(repo, providers(claude=[]), question="  ")
    with pytest.raises(R.ReportError):
        run(repo, providers(claude=[]), question="가" * (R.MAX_QUESTION + 1))
    with pytest.raises(ValueError):
        R.run_report(make_settings(), repo, "q", ["nobody"], providers=providers(claude=[]))


def test_project_context_is_sent_only_when_asked(repo):
    ps = providers(claude=[doc(1)])
    run(repo, ps, ctx=True)
    assert "PROJECT CONTEXT" in ps["claude"].calls[0]["prompt"]
    ps2 = providers(claude=[doc(1)])
    run(repo, ps2, ctx=False)
    assert "PROJECT CONTEXT" not in ps2["claude"].calls[0]["prompt"]


def test_listing_and_loading_validate_the_id(repo):
    res = run(repo, providers(claude=[doc(1), merged()], codex=[doc(2)]))
    rows = R.list_reports(repo, ".solomon")
    assert rows[0]["report_id"] == res["report_id"] and rows[0]["finished"] and rows[0]["status"] == "DONE" and rows[0]["title"] == "통합 보고서"
    view = R.load_report(repo, ".solomon", res["report_id"])
    assert view["finished"] and view["markdown"].startswith("# 통합 보고서") and set(view["drafts"]) == {"claude", "codex"}
    for bad in ("../../etc", "x", "20260101-000000-zzzzzz", ""):
        with pytest.raises(R.ReportError):
            R.load_report(repo, ".solomon", bad)
    assert R.load_report(repo, ".solomon", "20260101-000000-abcdef") is None


def test_cli_command_exists_and_is_a_job_command():
    from solomon import joblock
    parser = cli.build_parser()
    rp = next(a for a in parser._actions if a.dest == "command").choices["report"]
    assert {"--project", "--ais", "--context"} <= {o for a in rp._actions for o in a.option_strings}
    assert "report" in joblock.JOB_COMMANDS


# ---------------------------------------------------------------- dashboard
@pytest.fixture
def env(tmp_path):
    sleeper.calls.clear()
    e = Env(tmp_path)
    yield e
    e.close()


def test_dashboard_starts_a_report_job_and_validates(env):
    st, body, _ = env.call("POST", "/api/report", {"project": env.project, "question": "ena 봇 모델 육성 방법과 구현 방안", "ais": ["claude"], "context": True})
    assert st == 200
    args = sleeper.calls[-1]
    assert args[3] == "report" and "ena 봇 모델 육성 방법과 구현 방안" in args and args[args.index("--ais") + 1] == "claude" and "--context" in args
    env.app.job["popen"].kill(); env.app.job["popen"].wait()
    before = len(sleeper.calls)
    assert env.call("POST", "/api/report", {"project": env.project, "question": "  "})[0] == 400
    assert env.call("POST", "/api/report", {"project": env.project, "question": "q" * (R.MAX_QUESTION + 1)})[0] == 400
    assert env.call("POST", "/api/report", {"project": env.project, "question": "q", "ais": ["nobody"]})[0] == 400
    assert len(sleeper.calls) == before


def test_dashboard_serves_the_report_as_safe_html_and_needs_the_token(env):
    repo = Path(env.project)
    res = run(repo, providers(claude=[doc(1) + "\n<script>alert(1)</script>\n", merged()], codex=[doc(2)]))
    q = lambda **k: "/api/report?project=" + quote(env.project) + "".join(f"&{a}={quote(str(b))}" for a, b in k.items())
    assert env.call("GET", q(id=res["report_id"]), token=None)[0] == 401
    st, view, _ = env.call("GET", q(id=res["report_id"]))
    assert st == 200 and "<h1" in view["html"] and "<table" in view["html"] and view["finished"]
    st, view, _ = env.call("GET", "/api/reports?project=" + quote(env.project))
    assert st == 200 and view["reports"][0]["report_id"] == res["report_id"]
    d = env.call("GET", q(id=res["report_id"]))[1]["drafts_html"]
    assert "<script>" not in d["claude"] and "&lt;script&gt;" in d["claude"]  # a draft cannot inject markup into the page
    assert env.call("GET", q(id="../../etc"))[0] == 400 and env.call("GET", q(id="20260101-000000-abcdef"))[0] == 404


def test_the_report_tab_is_wired():
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    for needle in ('id="tab-rp"', 'id="view-rp"', 'id="rp-q"', 'id="rp-go"', 'id="rp-body"', '"/api/report"', "/api/reports?project=", "rpOpen()", 'if (t === "rp") rpOpen();',
                   '["work", "mg", "pj", "bot", "rp"]', "window.print()", "report: \"보고서\""):
        assert needle in html, needle


# ---------------------------------------------------------------- folder management
def test_export_copies_the_report_into_a_chosen_home_folder_and_never_overwrites(repo, tmp_path):
    import os
    res = run(repo, providers(claude=[doc(1), merged()], codex=[doc(2)]))
    out = Path(os.environ["HOME"]) / "Documents" / "reports"
    out.mkdir(parents=True)
    a = R.export_report(repo, ".solomon", res["report_id"], str(out))
    b = R.export_report(repo, ".solomon", res["report_id"], str(out))
    assert a != b and a.read_text().startswith("# 통합 보고서") and b.name.endswith("-2.md") and a.name.startswith("통합 보고서-" + res["report_id"])
    for bad in (str(tmp_path / "outside"), "relative/path", "", str(out / "missing")):
        with pytest.raises(R.ReportError):
            R.export_report(repo, ".solomon", res["report_id"], bad)
    (tmp_path / "outside").mkdir()
    with pytest.raises(R.ReportError, match="홈 폴더"):
        R.export_report(repo, ".solomon", res["report_id"], str(tmp_path / "outside"))  # not inside the home folder
    with pytest.raises(R.ReportError):
        R.export_report(repo, ".solomon", "../../x", str(out))


def test_delete_removes_exactly_one_report_and_cannot_reach_anything_else(repo):
    one = run(repo, providers(claude=[doc(1)]))
    two = run(repo, providers(claude=[doc(2)]))
    keep = repo / "README.md"
    R.delete_report(repo, ".solomon", one["report_id"])
    assert [r["report_id"] for r in R.list_reports(repo, ".solomon")] == [two["report_id"]] and keep.exists()
    for bad in ("../../..", "..", ".", "x", "", "20260101-000000-abcdef"):
        with pytest.raises(R.ReportError):
            R.delete_report(repo, ".solomon", bad)
    assert (repo / ".solomon" / "reports" / two["report_id"]).is_dir()


def test_dashboard_delete_and_export_endpoints(env, tmp_path):
    import os
    repo = Path(env.project)
    res = run(repo, providers(claude=[doc(1)]))
    out = Path(os.environ["HOME"]) / "exports"
    out.mkdir()
    st, body, _ = env.call("POST", "/api/report/export", {"project": env.project, "id": res["report_id"], "folder": str(out)})
    assert st == 200 and Path(body["path"]).read_text().startswith("# 제목 1") and Path(body["path"]).parent == out.resolve()
    assert env.call("POST", "/api/report/export", {"project": env.project, "id": res["report_id"], "folder": "/etc"})[0] == 400
    assert env.call("POST", "/api/report/export", {"project": "/nowhere", "id": res["report_id"], "folder": str(out)})[0] == 400
    assert env.call("POST", "/api/report/delete", {"project": env.project, "id": "../../x"})[0] == 400
    assert env.call("POST", "/api/report/delete", {"project": env.project, "id": "20260101-000000-abcdef"})[0] == 404
    assert env.call("POST", "/api/report/delete", {"project": env.project, "id": res["report_id"]})[0] == 200
    assert R.list_reports(repo, ".solomon") == []
    q = "/api/report?project=" + quote(env.project) + "&id=" + res["report_id"]
    assert env.call("GET", q)[0] == 404
    assert env.call("POST", "/api/report/delete", {"project": env.project, "id": res["report_id"]}, token=None)[0] == 401


def test_a_report_that_is_being_written_cannot_be_deleted(env):
    repo = Path(env.project)
    rid = "29990101-000000-aaaaaa"
    d = repo / ".solomon" / "reports" / rid
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text(json.dumps({"ts": "2026-11-21T08:00:00", "event": "report_start", "question": "q", "ais": ["claude"]}) + "\n")
    assert env.call("POST", "/api/report", {"project": env.project, "question": "q", "ais": ["claude"]})[0] == 200
    st, body, _ = env.call("POST", "/api/report/delete", {"project": env.project, "id": rid})
    assert st == 409 and "작성 중" in body["error"] and d.is_dir()
    env.app.job["popen"].kill(); env.app.job["popen"].wait()


def test_the_report_tab_has_folder_management():
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    for needle in ('id="rp-project" list="rp-projects"', 'id="rp-browse"', 'id="rp-openfolder"', 'id="rp-export"', 'id="rp-del"', "/api/report/export", "/api/report/delete",
                   'openBrowser("보고서를 저장할 프로젝트 폴더 고르기"', "openOnDisk(target)", "저장 위치: "):
        assert needle in html, needle
    assert '<select id="rp-project">' not in html


def test_the_folder_picker_falls_back_to_home_instead_of_a_dead_end():
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    assert '그 폴더는 열 수 없어 홈 폴더에서 시작합니다' in html and 'return load("")' in html


# ---------------------------------------------------------------- report -> small improvement tasks
SPLIT = json.dumps({"tasks": [
    {"title": "엘리엇 파동 탐지 함수", "goal": "ena_bot/elliott_wave.py 에 지그재그 기반 충격파(1-5) 탐지 함수를 만든다. 입력은 봉 목록, 출력은 파동 후보와 무효화 가격. 규칙 위반 테스트를 tests/test_elliott_wave.py 에 쓴다.",
     "size": "S", "files": ["ena_bot/elliott_wave.py", "tests/test_elliott_wave.py"], "depends_on": [], "verify": "새 테스트와 전체 테스트가 통과한다"},
    {"title": "차트 오버레이", "goal": "대시보드 차트 API 응답에 위 탐지 결과를 넣고 index.html 에서 파동 선과 라벨을 그린다. 출처 링크와 조회일을 문서에 기록한다.",
     "size": "M", "files": ["ena_bot/dashboard.py"], "depends_on": [1, 9], "verify": "API 응답 테스트"},
    {"title": "", "goal": "제목이 없어 버려진다 " * 5}], "human_steps": ["브라우저에서 차트가 겹치는지 눈으로 확인"]}, ensure_ascii=False)


def tasks_repo(repo, steps=None):
    res = run(repo, providers(claude=[doc(1), merged()], codex=[doc(2)]))
    return res


def test_a_report_is_split_into_small_tasks_with_the_impossible_parts_moved_to_the_person(repo):
    res = tasks_repo(repo)
    out = R.plan_tasks(make_settings(), repo, res["report_id"], providers=providers(claude=[SPLIT]))
    assert out["provider"] == "claude" and [t["id"] for t in out["tasks"]] == [1, 2] and out["tasks"][1]["depends_on"] == [1]  # 9 does not exist, the untitled one is gone
    assert "출처 링크" not in out["tasks"][1]["goal"] and any("출처 링크" in h for h in out["human_steps"]) and "브라우저에서 차트가" in " ".join(out["human_steps"])
    assert R.load_tasks(repo, ".solomon", res["report_id"])["tasks"][0]["title"] == "엘리엇 파동 탐지 함수"


def test_the_splitter_is_told_the_builder_limits_and_gets_the_report(repo):
    res = tasks_repo(repo)
    ps = providers(claude=[SPLIT])
    R.plan_tasks(make_settings(), repo, res["report_id"], providers=ps)
    prompt = ps["claude"].calls[0]["prompt"]
    assert "OFFLINE" in prompt and "통합 보고서" in prompt and "REPORT:" in prompt and "at most about 5 files" in prompt


def test_the_next_ai_splits_when_the_first_cannot(repo):
    res = tasks_repo(repo)
    out = R.plan_tasks(make_settings(), repo, res["report_id"], providers=providers(claude=["not json at all"], codex=[SPLIT]))
    assert out["provider"] == "codex"
    with pytest.raises(R.ReportError, match="나누지 못했습니다"):
        R.plan_tasks(make_settings(), repo, res["report_id"], providers=providers(claude=["x"], codex=["{}"]))
    with pytest.raises(R.ReportError, match="나눌 보고서가 없습니다"):
        R.plan_tasks(make_settings(), repo, "20260101-000000-abcdef", providers=providers(claude=[SPLIT]))


def test_a_task_becomes_a_marked_goal_and_its_state_follows_its_run(repo):
    res = tasks_repo(repo)
    rid = res["report_id"]
    R.plan_tasks(make_settings(), repo, rid, providers=providers(claude=[SPLIT]))
    goal = R.task_goal_text(repo, ".solomon", rid, 1)
    assert goal.startswith(f"[보고서 {rid} · 단계 1/2] 엘리엇 파동 탐지 함수") and "ena_bot/elliott_wave.py" in goal and "이 작업 하나만 구현하세요" in goal and "확인 방법:" in goal
    tasks = R.load_tasks(repo, ".solomon", rid)["tasks"]
    assert R.task_statuses(repo, ".solomon", rid, tasks)[1]["state"] == "todo"
    # a run that carries the marker
    from solomon.orchestrator import Orchestrator
    from helpers import write_file as wf, order_json as oj, review_json as rj
    o = Orchestrator(make_settings(), {"claude": ScriptedProvider("claude", [oj(), rj()]), "codex": ScriptedProvider("codex", [wf("src/new.py")])})
    r = o.run(goal, repo)
    assert r.status == "PASSED"
    st = R.task_statuses(repo, ".solomon", rid, tasks)
    assert st[1]["state"] == "passed" and st[1]["run_id"] == r.run_id and st[2]["state"] == "todo"
    (Path(r.run_dir) / "applied.json").write_text("{}")
    assert R.task_statuses(repo, ".solomon", rid, tasks)[1]["state"] == "applied"


def test_editing_a_task_goal_is_validated(repo):
    res = tasks_repo(repo)
    rid = res["report_id"]
    R.plan_tasks(make_settings(), repo, rid, providers=providers(claude=[SPLIT]))
    R.save_task_goal(repo, ".solomon", rid, 1, "새 지시문입니다. 충분히 길게 씁니다 ena_bot/x.py 에 함수를 만든다.")
    assert "새 지시문" in R.task_goal_text(repo, ".solomon", rid, 1)
    for bad in ("짧음", "x" * (R.MAX_TASK_GOAL + 1)):
        with pytest.raises(R.ReportError):
            R.save_task_goal(repo, ".solomon", rid, 1, bad)
    with pytest.raises(R.ReportError):
        R.save_task_goal(repo, ".solomon", rid, 99, "충분히 긴 지시문입니다. 그러나 없는 작업입니다 ena_bot/x.py")


def test_dashboard_pipeline_from_report_to_a_builder_run(env):
    repo = Path(env.project)
    res = run(repo, providers(claude=[doc(1), merged()], codex=[doc(2)]))
    rid = res["report_id"]
    st, body, _ = env.call("POST", "/api/report/tasks", {"project": env.project, "id": rid, "ai": "claude"})
    assert st == 200 and sleeper.calls[-1][3] == "report-tasks" and "--id" in sleeper.calls[-1] and "claude" in sleeper.calls[-1]
    env.app.job["popen"].kill(); env.app.job["popen"].wait()
    assert env.call("POST", "/api/report/task-run", {"project": env.project, "id": rid, "task_id": 1})[0] == 400  # not split yet
    R.plan_tasks(make_settings(), repo, rid, providers=providers(claude=[SPLIT]))
    q = "/api/report?project=" + quote(env.project) + "&id=" + rid
    view = env.call("GET", q)[1]
    assert [t["state"] for t in view["tasks"]["tasks"]] == ["todo", "todo"] and view["tasks"]["human_steps"]
    st, body, _ = env.call("POST", "/api/report/task-edit", {"project": env.project, "id": rid, "task_id": 2, "goal": "수정한 지시문입니다. ena_bot/dashboard.py 에 오버레이를 만든다."})
    assert st == 200
    assert env.call("POST", "/api/report/task-edit", {"project": env.project, "id": rid, "task_id": 2, "goal": "짧"})[0] == 400
    st, body, _ = env.call("POST", "/api/report/task-run", {"project": env.project, "id": rid, "task_id": 2})
    assert st == 200
    args = sleeper.calls[-1]
    assert args[3] == "run" and args[4].startswith(f"[보고서 {rid} · 단계 2/2] 차트 오버레이") and "수정한 지시문입니다" in args[4]
    env.app.job["popen"].kill(); env.app.job["popen"].wait()
    assert env.call("POST", "/api/report/task-run", {"project": env.project, "id": rid, "task_id": 9})[0] == 400
    assert env.call("POST", "/api/report/task-run", {"project": env.project, "id": "../x", "task_id": 1})[0] == 400
    assert env.call("POST", "/api/report/tasks", {"project": env.project, "id": rid}, token=None)[0] == 401


def test_the_report_tab_has_the_task_pipeline():
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    for needle in ('id="rp-tasks"', 'id="rp-task-make"', '"/api/report/tasks"', '"/api/report/task-run"', '"/api/report/task-edit"', "개선 화면에서 열기", "관리 탭에서 적용",
                   "먼저 적용:", "사람이 할 일", "taskRunning", 'reporttasks: "작업 나누기"', '"tool", "tests", "report", "reporttasks", "external"'):
        assert needle in html, needle
    from solomon import cli, joblock
    assert "report-tasks" in joblock.JOB_COMMANDS
    assert "report-tasks" in next(a for a in cli.build_parser()._actions if a.dest == "command").choices


# ---------------------------------------------------------------- unattended chain of report tasks
def make_tasks(repo, deps=((), (1,), ())):
    """A finished report with three tasks (2 needs 1; 3 is independent)."""
    res = run(repo, providers(claude=[doc(1)]))
    data = {"report_id": res["report_id"], "created": "2026-11-21 00:00:00", "provider": "claude", "human_steps": [],
            "tasks": [{"id": i + 1, "title": f"작업 {i + 1}", "goal": f"작업 {i + 1} 을 구현한다. src/t{i + 1}.py 에 함수를 만들고 테스트를 쓴다.", "size": "S",
                       "files": [f"src/t{i + 1}.py"], "depends_on": list(d), "verify": "테스트 통과"} for i, d in enumerate(deps)]}
    (repo / ".solomon" / "reports" / res["report_id"] / "tasks.json").write_text(json.dumps(data, ensure_ascii=False))
    return res["report_id"]


def chain_orch(settings, claude, codex):
    from solomon.orchestrator import Orchestrator
    return Orchestrator(settings, {"claude": ScriptedProvider("claude", claude), "codex": ScriptedProvider("codex", codex)})


def gitout(repo, *a):
    import subprocess
    return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, check=True).stdout


def new_settings(**kw):
    s = make_settings(**kw)
    s.test_command = ["python3", "-c", "import pathlib,sys; sys.exit(0 if pathlib.Path('src/new.py').exists() else 1)"]
    return s


def test_the_chain_builds_every_task_on_a_new_branch_each_on_top_of_the_last(repo):
    from solomon import autofix
    rid = make_tasks(repo)
    s = new_settings()
    ok = [json.dumps({"allowed_paths": ["src/"], "protected_paths": [], "acceptance_criteria": ["ok"], "allowed_deletions": [], "context": "t"}), json.dumps({"verdict": "PASS", "issues": [], "summary": "ok"})]
    from helpers import write_file as wf
    o = chain_orch(s, ok * 3, [wf("src/new.py"), wf("src/t2.py"), wf("src/t3.py")])
    head = gitout(repo, "rev-parse", "HEAD").strip()
    r = autofix.run_task_chain(s, repo, rid, orchestrator=o)
    assert r.status == "DONE" and [i.status for i in r.items] == ["PASSED"] * 3 and r.branch.startswith("solomon/chain-")
    tip = gitout(repo, "ls-tree", "-r", "--name-only", r.branch).split()
    assert {"src/new.py", "src/t2.py", "src/t3.py"} <= set(tip)  # the last task sees the first one's files: they are stacked
    assert gitout(repo, "rev-parse", "HEAD").strip() == head and not (repo / "src" / "t2.py").exists() and gitout(repo, "status", "--porcelain").strip() == ""
    log = gitout(repo, "log", "--format=%s", r.branch).splitlines()
    assert log[0].startswith("report task 3") and log[1].startswith("report task 2") and log[2].startswith("report task 1")
    assert "단계 1: 작업 1" == r.items[0].title and "3단계 중 3단계 통과" in r.summary
    from solomon import report as RR
    st = RR.task_statuses(repo, ".solomon", rid, RR.load_tasks(repo, ".solomon", rid)["tasks"])
    assert [st[i]["state"] for i in (1, 2, 3)] == ["passed"] * 3 and all(st[i]["batch_id"] == r.batch_id for i in (1, 2, 3))  # the screen can send you to the chain to apply them
    goals = [c["prompt"] for c in o.providers["claude"].calls if "USER GOAL" in c["prompt"]]
    assert f"[보고서 {rid} · 단계 1/3]" in goals[0] and f"[보고서 {rid} · 단계 2/3]" in goals[1]


def test_a_failed_task_skips_what_depends_on_it_and_the_independent_one_still_runs(repo):
    from solomon import autofix
    rid = make_tasks(repo)
    s = new_settings(max_attempts=1)
    plan = json.dumps({"allowed_paths": ["src/"], "protected_paths": [], "acceptance_criteria": ["ok"], "allowed_deletions": [], "context": "t"})
    fail, ok = json.dumps({"verdict": "FAIL", "issues": ["no"], "summary": "x"}), json.dumps({"verdict": "PASS", "issues": [], "summary": "ok"})
    from helpers import write_file as wf
    o = chain_orch(s, [plan, fail, plan, ok], [wf("src/new.py"), wf("src/new.py", "y = 2\n")])
    r = autofix.run_task_chain(s, repo, rid, orchestrator=o)
    assert [i.status for i in r.items] == ["FAILED", "SKIPPED", "PASSED"] and r.status == "PARTIAL"
    assert "먼저 끝나야 할 단계 1" in r.items[1].summary and "3단계 중 1단계 통과" in r.summary


def test_the_chain_stops_after_three_failures_in_a_row(repo):
    from solomon import autofix
    rid = make_tasks(repo, deps=((), (), (), (), ()))
    s = new_settings(max_attempts=1)
    plan = json.dumps({"allowed_paths": ["src/"], "protected_paths": [], "acceptance_criteria": ["ok"], "allowed_deletions": [], "context": "t"})
    fail = json.dumps({"verdict": "FAIL", "issues": ["no"], "summary": "x"})
    from helpers import write_file as wf
    o = chain_orch(s, [plan, fail] * 3, [wf("src/new.py")] * 3)
    r = autofix.run_task_chain(s, repo, rid, orchestrator=o)
    assert [i.status for i in r.items] == ["FAILED"] * 3 + ["SKIPPED"] * 2 and "3단계 연속 실패" in r.summary and not r.patch_path


def test_the_chain_refuses_without_tasks_without_tests_or_when_nothing_is_left(repo):
    from solomon import autofix
    res = run(repo, providers(claude=[doc(1)]))
    with pytest.raises(autofix.AutofixError, match="작업 목록이 없습니다"):
        autofix.run_task_chain(new_settings(), repo, res["report_id"])
    rid = make_tasks(repo)
    s = new_settings()
    s.test_command = None
    with pytest.raises(autofix.AutofixError, match="테스트 명령이 없으면"):
        autofix.run_task_chain(s, repo, rid)
    with pytest.raises(autofix.AutofixError, match="실행할 작업이 없습니다"):
        autofix.run_task_chain(new_settings(), repo, rid, [99])


def test_a_phone_message_when_the_night_is_over_only_when_bark_is_set_up(tmp_path):
    from solomon import alerts as A
    sent = []
    fake = lambda u, p: (sent.append(p) or (200, {"code": 200}))
    lab = tmp_path / "lab"
    assert A.notify_job_end("t", "b", lab, http=fake) is False and not sent  # no key saved: nothing happens
    a = A.Alerts(lab, http=fake)
    a.update({"device_key": "AbCdEfGh12345678XyZ"})
    assert A.notify_job_end("🏁 Solomon 순차 실행 DONE", "3단계 중 3단계 통과", lab, http=fake) is True
    assert sent[-1]["title"] == "🏁 Solomon 순차 실행 DONE" and "3단계 중 3단계" in sent[-1]["body"]
    a.update({"job_done": False})
    n = len(sent)
    assert A.notify_job_end("x", "y", lab, http=fake) is False and len(sent) == n  # the person switched it off
    assert A.notify_job_end("x", "y", lab, http=lambda u, p: (_ for _ in ()).throw(OSError("down"))) is False  # a failing push never raises


def test_dashboard_starts_the_chain_as_an_unattended_job_and_validates(env):
    repo = Path(env.project)
    rid = make_tasks(repo)
    st, body, _ = env.call("POST", "/api/report/chain", {"project": env.project, "id": rid, "task_ids": [1, 3], "budget_hours": 2})
    assert st == 200
    args = sleeper.calls[-1]
    assert args[3] == "report-chain" and args[args.index("--tasks") + 1] == "1,3" and args[args.index("--budget-minutes") + 1] == "120" and "--test-command" in args
    assert body["job"]["kind"] == "autofix"  # shown with the other unattended jobs
    env.app.job["popen"].kill(); env.app.job["popen"].wait()
    for bad in ({"task_ids": []}, {"task_ids": ["a"]}, {"budget_hours": 0}, {"budget_hours": 99}, {"id": "../x"}):
        assert env.call("POST", "/api/report/chain", {"project": env.project, "id": rid, **bad})[0] == 400, bad
    assert env.call("POST", "/api/report/chain", {"project": env.project, "id": rid}, token=None)[0] == 401
    res2 = run(repo, providers(claude=[doc(1)]))
    assert env.call("POST", "/api/report/chain", {"project": env.project, "id": res2["report_id"]})[0] == 400  # not split yet


def test_the_report_tab_has_the_unattended_chain():
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    for needle in ('id="rp-chain"', 'id="rp-chain-go"', 'id="rp-chain-h"', '"/api/report/chain"', "자리를 비워도 순서대로 자동 실행", "원본은 바뀌지 않습니다", "아이폰 알림",
                   'key: t.batch_id && t.state !== "failed" ? "batch:" + t.batch_id'):
        assert needle in html, needle
    from solomon import cli, joblock
    assert "report-chain" in joblock.JOB_COMMANDS and "report-chain" in next(a for a in cli.build_parser()._actions if a.dest == "command").choices


# ---------------------------------------------------------------- the tasks can be built in another project than the one holding the report
def two_projects(tmp_path):
    other = tmp_path / "other-bot"
    other.mkdir()
    import subprocess
    for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *cmd], cwd=other, check=True)
    (other / "README.md").write_text("bot\n")
    subprocess.run(["git", "add", "."], cwd=other, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=other, check=True)
    return other


def test_status_and_runs_follow_the_target_project(repo, tmp_path):
    other = two_projects(tmp_path)
    rid = make_tasks(repo)
    R.set_task_target(repo, ".solomon", rid, str(other))
    assert R.load_tasks(repo, ".solomon", rid)["target"] == str(other)
    tasks = R.load_tasks(repo, ".solomon", rid)["tasks"]
    assert R.task_statuses(repo, ".solomon", rid, tasks, str(other))[1]["state"] == "todo"
    goal = R.task_goal_text(repo, ".solomon", rid, 1)  # the goal comes from the report's project ...
    from solomon.orchestrator import Orchestrator
    from helpers import write_file as wf, order_json as oj, review_json as rj
    o = Orchestrator(make_settings(), {"claude": ScriptedProvider("claude", [oj(), rj()]), "codex": ScriptedProvider("codex", [wf("src/new.py")])})
    r = o.run(goal, other)  # ... and the work is done in the other one
    assert r.status == "PASSED" and (Path(r.run_dir).parent.parent.parent == other / ".solomon" or str(other) in r.run_dir)
    assert R.task_statuses(repo, ".solomon", rid, tasks)[1]["state"] == "todo"  # looking in the report's project finds nothing
    assert R.task_statuses(repo, ".solomon", rid, tasks, str(other))[1]["state"] == "passed"  # looking in the target finds the run


def test_the_chain_builds_in_the_target_and_reads_tasks_from_the_report_project(repo, tmp_path):
    from solomon import autofix
    other = two_projects(tmp_path)
    rid = make_tasks(repo, deps=((), ()))
    s = new_settings()
    ok = [json.dumps({"allowed_paths": ["src/"], "protected_paths": [], "acceptance_criteria": ["ok"], "allowed_deletions": [], "context": "t"}), json.dumps({"verdict": "PASS", "issues": [], "summary": "ok"})]
    from helpers import write_file as wf
    o = chain_orch(s, ok * 2, [wf("src/new.py"), wf("src/t2.py")])
    r = autofix.run_task_chain(s, other, rid, orchestrator=o, report_project=repo)
    assert r.status == "DONE" and (other / ".solomon" / "batches" / r.batch_id).is_dir() and not (repo / ".solomon" / "batches").exists()
    assert "src/t2.py" in gitout(other, "ls-tree", "-r", "--name-only", r.branch)
    assert gitout(repo, "branch", "--list", "solomon/chain-*").strip() == ""  # nothing was built in the project that only holds the report


def test_dashboard_runs_and_chains_in_the_chosen_target(env, tmp_path):
    repo = Path(env.project)
    other = two_projects(tmp_path)
    env.app.add_project(str(other))
    rid = make_tasks(repo)
    st, body, _ = env.call("POST", "/api/report/target", {"project": env.project, "id": rid, "target": str(other.resolve())})
    assert st == 200
    assert env.call("POST", "/api/report/target", {"project": env.project, "id": rid, "target": "/nowhere"})[0] == 400
    q = "/api/report?project=" + quote(env.project) + "&id=" + rid
    assert env.call("GET", q)[1]["tasks"]["target"] == str(other.resolve())
    st, body, _ = env.call("POST", "/api/report/task-run", {"project": env.project, "id": rid, "task_id": 1})
    assert st == 200
    args = sleeper.calls[-1]
    assert args[3] == "run" and args[args.index("--project") + 1] == str(other.resolve()) and args[4].startswith(f"[보고서 {rid} · 단계 1/3]")
    env.app.job["popen"].kill(); env.app.job["popen"].wait()
    st, body, _ = env.call("POST", "/api/report/chain", {"project": env.project, "id": rid})
    assert st == 200
    args = sleeper.calls[-1]
    assert args[3] == "report-chain" and args[args.index("--project") + 1] == str(other.resolve()) and args[args.index("--report-project") + 1] == env.project
    assert body["job"]["project"] == str(other.resolve())
    env.app.job["popen"].kill(); env.app.job["popen"].wait()


def test_the_report_tab_shows_and_changes_the_target():
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    for needle in ('id="rp-target-box"', 'id="rp-target-pick"', '"/api/report/target"', "작업을 실행할 프로젝트", "🎯 대상 프로젝트: ", "질문에 ENA가 들어 있는데 실행 대상이", "selectedProject = targetName()"):
        assert needle in html, needle
