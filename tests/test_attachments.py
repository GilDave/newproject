import base64
import json
import os
from pathlib import Path
from urllib.parse import quote

import pytest

from solomon import attachments as AT
from solomon import cli
from solomon.orchestrator import Orchestrator
from solomon.providers.claude import ClaudeProvider
from solomon.providers.codex import CodexProvider

from helpers import ScriptedProvider, make_repo, make_settings, order_json, review_json, write_file
from test_dashboard import Env, sleeper
from test_visual_gate import events

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
b64 = lambda b: base64.b64encode(b).decode()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


# ---------------------------------------------------------------- the checks
def test_only_real_pictures_pass_and_data_urls_are_accepted():
    assert AT.sniff(PNG) == "png" and AT.sniff(JPG) == "jpg" and AT.sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp" and AT.sniff(b"GIF89a....") == "gif"
    assert AT.sniff(b"<html>not an image</html>") == "" and AT.sniff(b"#!/bin/sh\nrm -rf /") == ""
    out = AT.decode([{"name": "a.png", "data": "data:image/png;base64," + b64(PNG)}, {"name": "b.jpg", "data": b64(JPG)}])
    assert [e for e, _ in out] == ["png", "jpg"]
    assert AT.decode(None) == [] and AT.decode([]) == []


@pytest.mark.parametrize("items,needle", [
    ([{"data": b64(b"hello world, plain text")}], "이미지만"),
    ([{"data": "###not base64###"}], "손상"),
    ([{"data": b64(PNG)}] * 5, "4장까지"),
    ([{"data": b64(PNG + b"x" * (AT.MAX_BYTES + 1))}], "너무 큽니다"),
    ("not a list", "형식"),
    ([{"nope": 1}], "손상|이미지만"),
])
def test_bad_attachments_are_refused_with_a_reason(items, needle):
    with pytest.raises(AT.AttachError) as e:
        AT.decode(items)
    import re
    assert e.value.status == 400 and re.search(needle, e.value.message)


def test_pictures_are_stored_outside_any_project_with_safe_names_and_old_ones_are_cleaned(tmp_path):
    root = tmp_path / "att"
    old = root / "20200101-000000-aaaaaa"
    old.mkdir(parents=True)
    os.utime(old, (1, 1))
    paths = AT.store([("png", PNG), ("jpg", JPG)], root)
    assert [p.name for p in paths] == ["img-1.png", "img-2.jpg"] and all(p.parent.parent == root for p in paths)
    assert not old.exists()  # older than a week
    assert AT.store([], root) == []
    with pytest.raises(AT.AttachError):
        AT.validate_files([str(tmp_path / "missing.png")])
    (tmp_path / "x.png").write_text("not really")
    with pytest.raises(AT.AttachError):
        AT.validate_files([str(tmp_path / "x.png")])
    assert AT.validate_files([str(paths[0])]) == [paths[0].resolve()]


# ---------------------------------------------------------------- the pipeline
class Eyes(ScriptedProvider):
    image_capable = True
    vision_verified = True


def shots(tmp_path):
    return [str(p) for p in AT.store([("png", PNG)], tmp_path / "att")]


def test_a_picture_goes_to_planner_builder_and_reviewer_that_can_see_and_is_kept_with_the_run(tmp_path):
    repo = make_repo(tmp_path)
    eyes = Eyes("claude", [order_json(), review_json()])
    codex = Eyes("codex", [write_file("src/new.py")])
    o = Orchestrator(make_settings(), {"claude": eyes, "codex": codex})
    r = o.run("화면에 이 오류가 나옵니다", repo, images=shots(tmp_path))
    assert r.status == "PASSED"
    kept = Path(r.run_dir) / "attachments" / "img-1.png"
    assert kept.read_bytes() == PNG
    plan, review = eyes.calls[0], eyes.calls[1]
    assert plan["images"] == [str(kept.resolve())] and "ATTACHED SCREENSHOTS" in plan["prompt"] and "LOOK at each one" in plan["prompt"]
    assert codex.calls[0]["images"] == [str(kept.resolve())] and codex.calls[0]["write"] is True  # the builder sees the error it must fix
    assert review["images"] == [str(kept.resolve())]
    assert any(e["event"] == "attachments" and e["names"] == ["img-1.png"] for e in events(r))


def test_an_ai_that_cannot_see_gets_a_note_not_the_picture(tmp_path):
    repo = make_repo(tmp_path)
    blind = ScriptedProvider("claude", [order_json(), review_json()])
    codex = ScriptedProvider("codex", [write_file("src/new.py")])
    r = Orchestrator(make_settings(), {"claude": blind, "codex": codex}).run("goal", repo, images=shots(tmp_path))
    assert r.status == "PASSED"
    for call in (blind.calls[0], blind.calls[1], codex.calls[0]):
        assert call["images"] is None and "cannot view images" in call["prompt"] and "do not guess" in call["prompt"]


def test_no_attachments_means_nothing_changes(tmp_path):
    repo = make_repo(tmp_path)
    p = ScriptedProvider("claude", [order_json(), review_json()])
    r = Orchestrator(make_settings(), {"claude": p, "codex": ScriptedProvider("codex", [write_file("src/new.py")])}).run("goal", repo)
    assert r.status == "PASSED" and all(c["images"] is None and "ATTACHED" not in c["prompt"] for c in p.calls)
    assert not (Path(r.run_dir) / "attachments").exists()


def test_the_status_lists_the_pictures_of_a_run(tmp_path):
    from solomon import status
    repo = make_repo(tmp_path)
    r = Orchestrator(make_settings(), {"claude": ScriptedProvider("claude", [order_json(), review_json()]), "codex": ScriptedProvider("codex", [write_file("src/new.py")])}).run("g", repo, images=shots(tmp_path))
    d = status.summarize_run(r.run_id, events(r)).to_dict()
    assert d["attachments"] == ["img-1.png"] and any("첨부한 화면 1장" in t for t in d["timeline"])


# ---------------------------------------------------------------- the CLIs get the folder and may read it
def test_claude_and_codex_command_lines_carry_the_pictures(tmp_path):
    c = ClaudeProvider()
    folder = tmp_path.resolve() / ".solomon" / "attachments" / "20261120-1"
    c._images = [str(folder / "img-1.png")]
    ro = c.build_argv("claude", False)
    assert ro[ro.index("--tools") + 1] == "Read" and ro[ro.index("--add-dir") + 1] == str(folder)
    wr = c.build_argv("claude", True)
    assert "--add-dir" in wr and "--permission-mode" in wr and "--tools" not in wr  # the builder keeps its normal tools
    c._images = []
    assert "--add-dir" not in c.build_argv("claude", True)
    x = CodexProvider()
    x._images = ["/a/img-1.png", "/a/img-2.png"]
    argv = x.build_argv("codex", True)
    assert argv.count("-i") == 2 and argv[-2:] == ["--", "-"] and "workspace-write" in argv


def test_cli_has_the_flag_and_refuses_a_file_that_is_not_a_picture(tmp_path, capsys):
    parser = cli.build_parser()
    run = next(a for a in parser._actions if a.dest == "command").choices["run"]
    assert "--image" in {o for a in run._actions for o in a.option_strings}
    bad = tmp_path / "x.png"
    bad.write_text("nope")
    repo = make_repo(tmp_path / "r")
    assert cli.main(["run", "goal", "--project", str(repo), "--image", str(bad)]) == 2
    assert "이미지 파일이 아닙니다" in capsys.readouterr().err


# ---------------------------------------------------------------- the dashboard
@pytest.fixture
def env(tmp_path):
    sleeper.calls.clear()
    e = Env(tmp_path)
    yield e
    e.close()


def test_run_request_stores_the_pictures_and_passes_them_to_the_job(env):
    st, body, _ = env.call("POST", "/api/run", {"goal": "화면의 오류를 고쳐줘", "project": env.project, "images": [{"name": "a.jpg", "data": b64(JPG)}, {"name": "b.png", "data": "data:image/png;base64," + b64(PNG)}]})
    assert st == 200
    args = sleeper.calls[-1]
    imgs = [args[i + 1] for i, a in enumerate(args) if a == "--image"]
    assert len(imgs) == 2 and all(Path(p).is_file() and ".solomon/attachments" in p for p in imgs)
    assert Path(imgs[0]).read_bytes() == JPG
    env.app.job["popen"].kill(); env.app.job["popen"].wait()


def test_a_bad_picture_stops_the_request_before_any_job_starts(env):
    before = len(sleeper.calls)
    st, body, _ = env.call("POST", "/api/run", {"goal": "x", "project": env.project, "images": [{"name": "a", "data": b64(b"just text, not a picture")}]})
    assert st == 400 and "이미지" in body["error"] and len(sleeper.calls) == before and env.app.job is None
    assert env.call("POST", "/api/run", {"goal": "x", "project": env.project, "images": [{"data": b64(PNG)}] * 5})[0] == 400


def test_attached_pictures_are_served_by_validated_name_and_need_the_token(env):
    d = Path(env.project) / ".solomon" / "runs" / "r1" / "attachments"
    d.mkdir(parents=True)
    (d / "img-1.png").write_bytes(PNG)
    (Path(env.project) / ".solomon" / "secret.png").write_bytes(b"nope")
    q = lambda **k: "/api/attach?project=" + quote(env.project) + "".join(f"&{a}={quote(str(b))}" for a, b in k.items())
    st, body, resp = env.call("GET", q(run="r1", name="img-1.png"))
    assert st == 200 and body == PNG and resp.getheader("Content-Type") == "image/png"
    assert env.call("GET", q(run="r1", name="img-1.png"), token=None)[0] == 401
    for bad in (dict(run="r1", name="../../secret.png"), dict(run="..", name="img-1.png"), dict(run="r1", name="other.png"), dict(run="r1/..", name="img-1.png")):
        assert env.call("GET", q(**bad))[0] in (400, 404), bad


def test_the_page_can_paste_drop_pick_and_show_the_pictures():
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    for needle in ('id="attBtn"', 'id="attFile"', 'id="attList"', 'addEventListener("paste"', 'addEventListener("drop"', "createImageBitmap", "/api/attach?t=", "images: att.map", "첨부한 화면"):
        assert needle in html, needle


# ---------------------------------------------------------------- one job at a time, across processes
def test_a_job_lock_held_by_another_process_blocks_new_jobs(env, tmp_path):
    from solomon import joblock
    assert not joblock.is_busy()
    held = joblock.acquire()
    assert held is not None and joblock.is_busy() and joblock.acquire() is None
    before = len(sleeper.calls)
    for path, body in (("/api/run", {"goal": "x", "project": env.project}), ("/api/analyze", {"project": env.project})):
        st, resp, _ = env.call("POST", path, body)
        assert st == 409 and "이미 실행 중" in resp["error"], path
    assert len(sleeper.calls) == before
    held.close()
    assert not joblock.is_busy()


def test_cli_job_commands_refuse_while_another_job_holds_the_lock(tmp_path, capsys):
    from solomon import joblock
    held = joblock.acquire()
    repo = make_repo(tmp_path / "r")
    assert cli.main(["run", "goal", "--project", str(repo)]) == 2
    assert "이미 실행 중" in capsys.readouterr().err
    held.close()


def test_the_page_shows_a_job_started_elsewhere_as_busy(env):
    from solomon import joblock
    assert env.app.job_info()["state"] == "idle"
    held = joblock.acquire()
    info = env.app.job_info()
    assert info["state"] == "running" and info["external"] and "다른 곳" in info["goal"]
    assert env.app.build_state(env.project, None)["job"]["external"]  # the buttons are disabled like for its own job
    held.close()
    assert env.app.job_info()["state"] == "idle"


def test_ollama_keeps_the_top_seat_even_without_a_role():
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    assert 'provs.some(p => p.name === "ollama") ? "ollama" : null' in html


def test_ai_cards_count_calls_by_their_own_day_and_include_analyses(env):
    from datetime import datetime, timedelta
    today = datetime.now().strftime("%Y-%m-%d")
    yest = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    root = Path(env.project) / ".solomon"
    run = root / "runs" / (yest.replace("-", "") + "-230000-aaaaaa")  # started yesterday evening, still calling after midnight
    run.mkdir(parents=True)
    ev = [{"ts": f"{yest}T23:00:00", "event": "run_start", "goal": "g", "roles": {}},
          {"ts": f"{yest}T23:30:00", "event": "call_end", "role": "builder", "provider": "codex", "duration_sec": 5},
          {"ts": f"{today}T00:10:00", "event": "call_end", "role": "builder", "provider": "codex", "duration_sec": 6},
          {"ts": f"{today}T00:20:00", "event": "call_end", "role": "reviewer", "provider": "codex", "duration_sec": 7}]
    (run / "events.jsonl").write_text("\n".join(json.dumps(e) for e in ev) + "\n")
    an = root / "analyses" / (today.replace("-", "") + "-010000-bbbbbb")
    an.mkdir(parents=True)
    (an / "events.jsonl").write_text(json.dumps({"ts": f"{today}T01:00:05", "event": "ai_end", "provider": "claude", "ok": True, "duration_sec": 30}) + "\n")
    cards = {c["name"]: c for c in env.app._provider_cards(__import__("solomon.status", fromlist=["x"]).load_runs(Path(env.project)), env.app.providers_info(), env.project)}
    assert cards["codex"]["calls_today"] == 2 and cards["codex"]["last_call"]["role"] == "reviewer"
    assert cards["claude"]["calls_today"] == 1 and cards["claude"]["last_call"]["role"] == "analysis"


# ---------------------------------------------------------------- the test environment
def test_the_projects_own_virtualenv_is_used_and_a_missing_module_gets_plain_help(tmp_path):
    from solomon.verifier import environment_hint, resolve_python
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    py = tmp_path / ".venv" / "bin" / "python"
    py.write_text("")
    assert resolve_python(["python3", "-m", "pytest", "-q"], tmp_path) == [str(py), "-m", "pytest", "-q"]
    assert resolve_python(["pytest"], tmp_path) == ["pytest"] and resolve_python(["/x/python3", "-m", "pytest"], tmp_path)[0] == "/x/python3"
    assert resolve_python(["python3", "-m", "pytest"], tmp_path / "nowhere") == ["python3", "-m", "pytest"] and resolve_python(None, tmp_path) is None
    msg = environment_hint("/opt/homebrew/opt/python@3.15/bin/python3.15 -m pytest -q", "/opt/homebrew/opt/python@3.15/bin/python3.15: No module named pytest")
    assert "'pytest' 모듈이 없습니다" in msg and "python3 -m venv .venv" in msg and "pip install pytest" in msg
    assert environment_hint("python3 -m pytest", "1 failed, 3 passed") == ""


def test_a_run_uses_the_project_venv_and_stops_with_help_when_pytest_is_missing(tmp_path):
    repo = make_repo(tmp_path)
    (repo / ".venv" / "bin").mkdir(parents=True)
    py = repo / ".venv" / "bin" / "python"
    py.write_text("#!/bin/sh\necho 'fake: No module named pytest' >&2\nexit 1\n")
    py.chmod(0o755)
    s = make_settings()
    s.test_command = ["python3", "-m", "pytest", "-q"]
    s.approved_commands = ["python", "python3", "pytest"]
    s.baseline_check = True
    claude, codex = ScriptedProvider("claude", [order_json()]), ScriptedProvider("codex", [])
    r = Orchestrator(s, {"claude": claude, "codex": codex}).run("goal", repo)
    assert r.status == "ERROR" and not codex.calls  # stopped before the builder
    assert s.test_command[0] == str(py.resolve()) and "'pytest' 모듈이 없습니다" in r.summary and ".venv/bin/pip install pytest" in r.summary


def test_the_work_room_has_the_same_stop_button_and_labels_its_scopes():
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    for needle in ('id="w-stop"', '$("w-stop").onclick = () => $("stop").click()', 'state.job.state !== "running" || !!state.job.external', "(보고 있는 실행 1건)",
                   '$("stop").disabled = !running || !!state.job.external'):
        assert needle in html, needle


def test_codex_read_only_calls_work_outside_a_git_folder_but_the_builder_keeps_the_check():
    x = CodexProvider()
    assert "--skip-git-repo-check" in x.build_argv("codex", False)  # the picture test and analysis of a plain folder
    assert "--skip-git-repo-check" not in x.build_argv("codex", True)  # the builder always works in a git worktree
    x._images = ["/a/img-1.png"]
    argv = x.build_argv("codex", False)
    assert "--skip-git-repo-check" in argv and argv[-2:] == ["--", "-"] and argv.index("--skip-git-repo-check") < argv.index("-i")


# ---------------------------------------------------------------- self-improvement: the test phase and the lock
def test_tests_never_use_the_real_job_lock(tmp_path):
    from solomon import joblock
    assert joblock.lock_path() == tmp_path / "test-job.lock"
    assert not joblock.is_busy()  # a Solomon job holding the real lock cannot make the project's own tests fail


def test_the_screen_says_when_the_whole_test_suite_runs_before_the_first_attempt(tmp_path):
    from solomon import status
    repo = make_repo(tmp_path)
    s = make_settings()
    s.test_command = ["python3", "-c", "print('ok')"]
    s.approved_commands = ["python3"]
    s.baseline_check = True
    r = Orchestrator(s, {"claude": ScriptedProvider("claude", [order_json(), review_json()]), "codex": ScriptedProvider("codex", [write_file("src/new.py")])}).run("goal", repo)
    evs = events(r)
    names = [e["event"] for e in evs]
    assert names.index("baseline_start") < names.index("baseline") < names.index("attempt_start")
    assert "seconds" in next(e for e in evs if e["event"] == "baseline")
    mid = status.summarize_run("x", evs[:names.index("baseline") ])
    assert mid.stage_key == "baseline" and "시작 전 점검" in mid.stage and any("수정 전 코드의 테스트를 돌립니다" in t for t in mid.timeline)
    done = status.summarize_run("x", evs[:names.index("attempt_start")])
    assert done.stage_key == "build" and any("시작 전 점검 통과" in t for t in done.timeline)


def test_the_now_card_names_the_test_phase():
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    assert '"수정 전 코드의 테스트를 돌리는 중입니다"' in html and "baseline:0.5" in html


def test_a_baseline_that_already_passed_on_the_same_commit_is_not_rerun(tmp_path):
    repo = make_repo(tmp_path)

    def one(steps):
        s = make_settings()
        s.test_command = ["python3", "-c", "print('ok')"]
        s.approved_commands = ["python3"]
        s.baseline_check = True
        return Orchestrator(s, {"claude": ScriptedProvider("claude", [order_json(), review_json()]), "codex": ScriptedProvider("codex", steps)}).run("goal", repo)

    first = events(one([write_file("src/new.py")]))
    assert [e["event"] for e in first if e["event"].startswith("baseline")] == ["baseline_start", "baseline"]
    second = events(one([write_file("src/other.py")]))
    assert [e["event"] for e in second if e["event"].startswith("baseline")] == ["baseline_cached"]  # same commit, same command: no minutes lost
    from solomon import status
    run = status.summarize_run("x", second)
    assert any("시작 전 점검 생략" in t for t in run.timeline)
    s = make_settings()
    s.test_command = ["python3", "-c", "print('changed command')"]
    s.approved_commands = ["python3"]
    s.baseline_check = True
    third = events(Orchestrator(s, {"claude": ScriptedProvider("claude", [order_json(), review_json()]), "codex": ScriptedProvider("codex", [write_file("src/third.py")])}).run("goal", repo))
    assert "baseline_start" in [e["event"] for e in third]  # another command is another question


def test_baseline_cache_expires_and_ignores_garbage(tmp_path):
    from solomon.verifier import BASELINE_TTL_SEC, baseline_known_good, baseline_remember
    f = tmp_path / "baseline.json"
    assert not baseline_known_good(f, "k", 1000.0)
    f.write_text("not json")
    assert not baseline_known_good(f, "k", 1000.0)
    baseline_remember(f, "k", 1000.0)
    assert baseline_known_good(f, "k", 1000.0 + 60) and not baseline_known_good(f, "k", 1000.0 + BASELINE_TTL_SEC + 1) and not baseline_known_good(f, "other", 1001.0)


def test_a_failed_baseline_says_which_tests_failed(tmp_path):
    from solomon import status
    from solomon.verifier import failing_tests
    out = "..F\n=== short test summary info ===\nFAILED tests/test_a.py::test_one - assert 1 == 2\nFAILED tests/test_a.py::test_one - again\nERROR tests/test_b.py::test_two\n2 failed, 5 passed"
    assert failing_tests(out) == ["tests/test_a.py::test_one", "tests/test_b.py::test_two"] and failing_tests("all fine") == []
    repo = make_repo(tmp_path)
    s = make_settings()
    s.test_command = ["python3", "-c", "import sys; print('FAILED tests/test_a.py::test_one - boom'); print('1 failed, 3 passed'); sys.exit(1)"]
    s.approved_commands = ["python3"]
    s.baseline_check = True
    codex = ScriptedProvider("codex", [])
    r = Orchestrator(s, {"claude": ScriptedProvider("claude", [order_json()]), "codex": codex}).run("goal", repo)
    assert r.status == "ERROR" and not codex.calls and "test_one" in r.summary and "Failing tests" in r.summary
    run = status.summarize_run("x", events(r))
    assert any("실패한 테스트: test_one" in t for t in run.timeline), run.timeline


def test_tests_in_a_virtualenv_find_that_environment_first(tmp_path):
    from solomon.verifier import verify
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    py = venv / "bin" / "python"
    py.write_text('#!/bin/sh\necho "PATH=$PATH"\necho "VENV=$VIRTUAL_ENV"\n')
    py.chmod(0o755)
    s = make_settings()
    s.test_command = [str(py)]
    s.approved_commands = ["python"]
    res = verify(s, tmp_path)
    assert res.passed and f"PATH={venv / 'bin'}:" in res.output_tail and f"VENV={venv}" in res.output_tail


def test_tests_never_read_the_machines_real_solomon_folder():
    from solomon import quota
    assert str(Path.home()).startswith("/tmp") or "pytest" in str(Path.home())  # not the real home of whoever runs the tests
    assert quota.QuotaBoard.persistent().path.parent.parent == Path.home()


def test_the_cli_diagram_keeps_its_animation_and_shows_when_solomon_itself_is_working():
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    for needle in ("if (svg.dataset.key === key) return;",  # a refresh must not restart a running animation
                   'path.style.animation = "flow 1s linear infinite"', 'attributeName: "r"', '"직접 작업 중 · "',
                   "animation-iteration-count:1 !important"):  # reduced motion: no flicker from a 0.01ms infinite loop
        assert needle in html, needle


def test_the_current_job_card_is_on_top_and_sticks_and_checkboxes_do_not_redraw_the_page():
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    main = html.index('<main class="grid" id="view-dash">')
    assert main < html.index('id="now"') < html.index('<section class="col">', main) < html.index('id="cmd"')  # first thing on the page, before the settings
    css = next(l for l in html.splitlines() if l.startswith("#now{"))
    assert "position:sticky" in css and "grid-column:1/-1" in css
    assert "if (box.dataset.key === key) return;" in html and "window.scrollTo(0, keepY)" in html  # an unchanged list is left alone; a redraw never moves the page
    assert "renderReport(state.analysis); }; h.appendChild(k)" not in html and "boxes.set(c.id, k)" in html and "refreshFix()" in html


def test_one_compact_vertical_rhythm_for_every_screen():
    html = (Path(__file__).parent.parent / "solomon" / "dashboard.html").read_text(encoding="utf-8")
    i = html.index("/* density (v1.15.5)")
    block = html[i:html.index("@media(max-width:1250px)", i)]
    for needle in (".grid,.pjw,.mg,.work,.rpw{padding:0 var(--gutter) 14px;gap:10px 12px}", ".card{padding:11px 14px}", "header{grid-template-columns:auto auto minmax(0,1fr) auto auto",
                   ".page-head{padding-block:8px 0}"):
        assert needle in block, needle  # the same numbers for the dashboard, work room, manage, projects, bot lab and report screens
    assert ".strip{grid-row:3;grid-column:1/-1}" in html  # on a narrow screen the status strip still gets its own row
