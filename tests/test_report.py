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
