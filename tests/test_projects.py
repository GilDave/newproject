import json
import subprocess
from pathlib import Path

import pytest

from solomon import projects
from solomon.settings import Settings

from test_dashboard import Env, sleeper


@pytest.fixture
def env(tmp_path):
    sleeper.calls.clear()
    root = tmp_path / "root"
    e = Env(tmp_path, settings=Settings(projects_root=str(root)))
    e.root = root
    yield e
    e.close()


def create(env, **kw):
    body = {"name": "my-app", "stack": "python", **kw}
    return env.call("POST", "/api/projects/create", body)


def git(path, *a):
    return subprocess.run(["git", *a], cwd=path, capture_output=True, text=True, check=True).stdout


def test_create_python_project_is_a_ready_git_repo_with_tests(env):
    st, body, _ = create(env, goal="")
    assert st == 200 and body["job"] is None
    path = Path(body["project"])
    assert path == env.root.resolve() / "my-app" and path.is_dir()
    assert git(path, "rev-list", "--count", "HEAD").strip() == "1"
    assert git(path, "log", "-1", "--format=%an").strip() == "Solomon"
    assert git(path, "status", "--porcelain").strip() == ""  # the marker folder is not git noise
    assert (path / ".solomon" / "project.json").is_file() and (path / "tests" / "test_smoke.py").is_file()
    assert ".env" in (path / ".gitignore").read_text()
    # registered, with its test command, and the scaffold's own tests pass
    info = next(p for p in env.call("GET", "/api/projects")[1]["projects"] if p["path"] == str(path))
    assert info["managed"] and info["git"] and info["test_command"] == "python3 -m pytest -q"
    assert subprocess.run(["python3", "-m", "pytest", "-q"], cwd=path, capture_output=True).returncode == 0


def test_create_with_goal_starts_a_first_run_without_the_baseline_check(env):
    st, body, _ = create(env, goal="간단한 계산기를 만들어줘", persist=True)
    assert st == 200 and body["job"]["kind"] == "run"
    args = sleeper.calls[-1]
    assert "--no-baseline" in args and "--persist" in args and body["project"] in args


def test_other_stacks(env):
    st, body, _ = create(env, name="web-thing", stack="node")
    assert (Path(body["project"]) / "package.json").is_file()
    st, body, _ = create(env, name="empty", stack="blank")
    assert not (Path(body["project"]) / "tests").exists()
    info = next(p for p in env.call("GET", "/api/projects")[1]["projects"] if p["name"] == "empty")
    assert info["test_command"] == ""


@pytest.mark.parametrize("name", ["", "a b", "../escape", "a/b", ".hidden", "x" * 61, "한글", "-lead"])
def test_bad_names_are_rejected_and_nothing_is_created(env, name):
    assert create(env, name=name)[0] == 400
    assert not env.root.exists() or not any(env.root.iterdir())


def test_duplicate_name_and_unknown_stack(env):
    assert create(env)[0] == 200
    assert create(env)[0] == 409
    assert create(env, name="other", stack="cobol")[0] == 400


def test_delete_needs_the_exact_name_and_goes_to_the_trash(env):
    path = Path(create(env)[1]["project"])
    (path / "src.py").write_text("keep me\n")
    assert env.call("POST", "/api/projects/delete", {"path": str(path), "confirm": "wrong"})[0] == 400
    assert path.exists()
    st, body, _ = env.call("POST", "/api/projects/delete", {"path": str(path), "confirm": "my-app"})
    assert st == 200 and not path.exists()
    ov = env.call("GET", "/api/projects")[1]
    assert str(path) not in [p["path"] for p in ov["projects"]] and ov["trash"][0]["name"] == "my-app"
    # restore brings everything back, files included, and re-lists it
    rid = ov["trash"][0]["id"]
    st, body, _ = env.call("POST", "/api/projects/restore", {"id": rid})
    assert st == 200 and (path / "src.py").read_text() == "keep me\n"
    ov = env.call("GET", "/api/projects")[1]
    assert str(path) in [p["path"] for p in ov["projects"]] and ov["trash"] == []


def test_restore_never_overwrites_and_purge_needs_the_name(env):
    path = Path(create(env)[1]["project"])
    env.call("POST", "/api/projects/delete", {"path": str(path), "confirm": "my-app"})
    rid = env.call("GET", "/api/projects")[1]["trash"][0]["id"]
    assert create(env)[0] == 200  # the name is taken again
    assert env.call("POST", "/api/projects/restore", {"id": rid})[0] == 409
    assert env.call("POST", "/api/projects/purge", {"id": rid, "confirm": "nope"})[0] == 400
    assert env.call("POST", "/api/projects/purge", {"id": rid, "confirm": "my-app"})[0] == 200
    assert env.call("GET", "/api/projects")[1]["trash"] == []
    assert path.exists()  # purging the trash copy did not touch the new folder with the same name


def test_only_projects_solomon_created_can_be_deleted(env, tmp_path):
    # an existing project (like the live bot) can only be removed from the list, never deleted
    assert env.call("POST", "/api/projects/delete", {"path": env.project, "confirm": Path(env.project).name})[0] == 403
    assert Path(env.project).is_dir() and (Path(env.project) / "src" / "app.py").exists()
    # a folder carrying the marker but outside the projects root is refused too
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ".solomon").mkdir()
    (outside / ".solomon" / "project.json").write_text(json.dumps({"created_by": "solomon"}))
    env.call("POST", "/api/project", {"path": str(outside)})
    assert env.call("POST", "/api/projects/delete", {"path": str(outside), "confirm": "outside"})[0] == 403
    assert outside.is_dir()
    # a symlink named like a managed project does not get us out of the root
    link = env.root / "linky"
    env.root.mkdir(exist_ok=True)
    link.symlink_to(outside)
    env.call("POST", "/api/project", {"path": str(link)})
    assert env.call("POST", "/api/projects/delete", {"path": str(link), "confirm": "outside"})[0] in (400, 403)
    assert outside.is_dir()


def test_remove_from_list_keeps_the_files(env):
    assert env.call("POST", "/api/projects/remove", {"path": env.project})[0] == 200
    assert Path(env.project).is_dir()
    assert env.project not in [p["path"] for p in env.call("GET", "/api/projects")[1]["projects"]]
    assert env.call("POST", "/api/projects/remove", {"path": env.project})[0] == 400


def test_no_delete_or_remove_while_a_job_runs_there(env):
    path = create(env)[1]["project"]
    env.call("POST", "/api/analyze", {"project": path})
    assert env.call("POST", "/api/projects/delete", {"path": path, "confirm": "my-app"})[0] == 409
    assert env.call("POST", "/api/projects/remove", {"path": path})[0] == 409
    assert Path(path).is_dir()


def test_trash_ids_cannot_escape(env):
    for bad in ["../../etc", "..", "/tmp", "x/../y"]:
        assert env.call("POST", "/api/projects/restore", {"id": bad})[0] == 404
        assert env.call("POST", "/api/projects/purge", {"id": bad, "confirm": "x"})[0] == 404


def test_endpoints_need_the_token(env):
    assert env.call("GET", "/api/projects", token=None)[0] == 401
    assert env.call("POST", "/api/projects/create", {"name": "x"}, token=None)[0] == 401


def test_cli_no_baseline_flag(monkeypatch, tmp_path):
    from solomon import cli
    seen = {}

    class FakeOrch:
        def __init__(self, settings, **kw):
            seen["b"] = settings.baseline_check

        def run(self, goal, project, keep_worktree=False):
            from solomon.orchestrator import RunResult
            return RunResult(run_id="r", status="PASSED", summary="ok", run_dir=str(tmp_path))

    monkeypatch.setattr(cli, "Orchestrator", FakeOrch)
    cli.main(["run", "g", "--project", str(tmp_path), "--no-baseline"])
    assert seen["b"] is False


def test_bad_roles_fail_before_any_folder_is_created(env):
    st, body, _ = create(env, goal="build it", roles={"builder": "antigravity"})
    assert st == 400 and not (env.root / "my-app").exists()
