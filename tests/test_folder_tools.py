import os
import stat
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest

from solomon import dashboard, folders
from solomon.settings import Settings

from test_dashboard import Env, sleeper


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SOLOMON_HOME", str(tmp_path / "solhome"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    sleeper.calls.clear()
    e = Env(tmp_path, settings=Settings(projects_root=str(tmp_path / "Solomon-Projects")))
    e.home = tmp_path
    yield e
    e.close()


def get(env, url, **q):
    qs = "&".join(f"{k}={quote(str(v))}" for k, v in q.items())
    return env.call("GET", url + ("?" + qs if qs else ""))


def post(env, path, body):
    return env.call("POST", path, body)


def made(env, name="my-app"):
    st, body, _ = post(env, "/api/projects/create", {"name": name, "stack": "python"})
    assert st == 200, body
    return body["project"]


# ------------------------------------------------------------------ browse
def test_browse_lists_visible_folders_inside_home_only(env):
    (env.home / "Library").mkdir()
    (env.home / ".ssh").mkdir()
    (env.home / "Documents").mkdir()
    (env.home / "a-file.txt").write_text("x")
    st, b, _ = get(env, "/api/browse")
    names = [d["name"] for d in b["dirs"]]
    assert st == 200 and b["path"] == str(env.home.resolve()) and b["parent"] == ""
    assert "Documents" in names and "proj" in names and "Library" not in names and ".ssh" not in names and "a-file.txt" not in names
    proj = next(d for d in b["dirs"] if d["name"] == "proj")
    assert proj["git"] and proj["registered"] and not proj["solomon"]
    st, sub, _ = get(env, "/api/browse", path=env.project)
    assert sub["parent"] == str(env.home.resolve()) and [c["name"] for c in sub["crumbs"]] == ["홈", "proj"] and "src" in [d["name"] for d in sub["dirs"]]


@pytest.mark.parametrize("target", ["/etc", "/", "{home}/..", "{home}/../../etc"])
def test_browse_refuses_to_leave_home(env, target):
    assert get(env, "/api/browse", path=target.format(home=env.home))[0] == 403


def test_browse_does_not_follow_symlinks_out_of_home(env, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "secret").mkdir()
    (env.home / "link").symlink_to(outside)
    assert "link" not in [d["name"] for d in get(env, "/api/browse")[1]["dirs"]]  # symlinked folders are not listed
    assert get(env, "/api/browse", path=env.home / "link")[0] == 403  # and walking into one is refused
    assert get(env, "/api/browse", path=env.home / "nothing")[0] == 404


def test_browse_needs_the_token(env):
    assert env.call("GET", "/api/browse", token=None)[0] == 401


# ------------------------------------------------------------------ rename
def test_rename_moves_the_folder_and_keeps_its_settings(env):
    old = made(env)
    post(env, "/api/test-command", {"project": old, "command": "python3 -m pytest -x"})
    st, body, _ = post(env, "/api/projects/rename", {"path": old, "name": "better-name"})
    new = body["project"]
    assert st == 200 and Path(new).name == "better-name" and Path(new).is_dir() and not Path(old).exists()
    ov = get(env, "/api/projects")[1]["projects"]
    row = next(p for p in ov if p["path"] == new)
    assert old not in [p["path"] for p in ov] and row["test_command"] == "python3 -m pytest -x" and row["managed"] and row["git"]
    assert subprocess.run(["git", "status", "--porcelain"], cwd=new, capture_output=True, text=True).stdout.strip() == ""


@pytest.mark.parametrize("name,code", [("", 400), ("a b", 400), ("../x", 400), (".hidden", 400), ("my-app", 400), ("other", 409)])
def test_rename_validation(env, name, code):
    old = made(env)
    made(env, "other")
    assert post(env, "/api/projects/rename", {"path": old, "name": name})[0] == code
    assert Path(old).is_dir()


def test_rename_is_only_for_folders_solomon_made_and_never_while_busy(env):
    assert post(env, "/api/projects/rename", {"path": env.project, "name": "new-name"})[0] == 403
    assert Path(env.project).is_dir()
    old = made(env)
    post(env, "/api/analyze", {"project": old})
    assert post(env, "/api/projects/rename", {"path": old, "name": "x-new"})[0] == 409


# ------------------------------------------------------------------ alias and memo
def test_alias_and_memo_never_touch_the_folder(env):
    before = sorted(p.name for p in Path(env.project).iterdir())
    st, _, _ = post(env, "/api/projects/meta", {"path": env.project, "alias": "실거래 봇", "memo": "장 마감 후에만 적용"})
    assert st == 200
    row = next(p for p in get(env, "/api/projects")[1]["projects"] if p["path"] == env.project)
    card = next(c for c in get(env, "/api/portfolio")[1]["projects"] if c["path"] == env.project)
    assert row["alias"] == "실거래 봇" and card["alias"] == "실거래 봇" and card["memo"] == "장 마감 후에만 적용"
    assert sorted(p.name for p in Path(env.project).iterdir()) == before
    post(env, "/api/projects/meta", {"path": env.project, "alias": "", "memo": ""})
    assert next(p for p in get(env, "/api/projects")[1]["projects"] if p["path"] == env.project)["alias"] == ""
    assert post(env, "/api/projects/meta", {"path": env.project, "alias": "x" * 41, "memo": ""})[0] == 400
    assert post(env, "/api/projects/meta", {"path": "/nope", "alias": "a", "memo": ""})[0] == 400


# ------------------------------------------------------------------ git init for a plain folder
def plain_folder(env):
    d = env.home / "old-project"
    (d / "src").mkdir(parents=True)
    (d / "src" / "main.py").write_text("print('hi')\n")
    (d / ".env").write_text("TOKEN=secret\n")
    (d / "key.pem").write_text("PRIVATE\n")
    (d / "node_modules" / "pkg").mkdir(parents=True)
    (d / "node_modules" / "pkg" / "x.js").write_text("x")
    (d / ".venv").mkdir()
    (d / ".venv" / "bin").write_text("x")
    post(env, "/api/project", {"path": str(d)})
    return d


def test_git_init_commits_the_work_but_never_secrets_or_bulky_folders(env):
    d = plain_folder(env)
    pre = get(env, "/api/projects/git-init-preview", path=str(d))[1]
    assert pre["files"] == 2 and "node_modules" in pre["excluded"] and ".venv" in pre["excluded"]
    st, body, _ = post(env, "/api/projects/git-init", {"path": str(d)})
    assert st == 200 and "파일 2개" in body["message"]
    tracked = subprocess.run(["git", "ls-files"], cwd=d, capture_output=True, text=True).stdout.split()
    assert "src/main.py" in tracked and ".gitignore" in tracked and ".env" not in tracked and "key.pem" not in tracked
    assert not any(t.startswith(("node_modules", ".venv")) for t in tracked)
    assert subprocess.run(["git", "log", "-1", "--format=%an"], cwd=d, capture_output=True, text=True).stdout.strip() == "Solomon"
    assert (d / ".env").read_text() == "TOKEN=secret\n"  # the files themselves are untouched
    row = next(p for p in get(env, "/api/projects")[1]["projects"] if p["path"] == str(d))
    assert row["git"] is True
    assert post(env, "/api/projects/git-init", {"path": str(d)})[0] == 409
    assert get(env, "/api/projects/git-init-preview", path=str(d))[0] == 409


def test_git_init_refuses_unknown_folders_and_busy_projects(env):
    assert post(env, "/api/projects/git-init", {"path": str(env.home)})[0] == 400  # not a registered project
    d = plain_folder(env)
    post(env, "/api/analyze", {"project": str(d)})
    assert post(env, "/api/projects/git-init", {"path": str(d)})[0] == 409


def test_an_existing_gitignore_is_extended_not_replaced(env):
    d = env.home / "has-ignore"
    d.mkdir()
    (d / ".gitignore").write_text("build/\n.env\n")
    (d / "a.py").write_text("x = 1\n")
    post(env, "/api/project", {"path": str(d)})
    post(env, "/api/projects/git-init", {"path": str(d)})
    lines = (d / ".gitignore").read_text().splitlines()
    assert lines[:2] == ["build/", ".env"] and lines.count(".env") == 1 and "node_modules/" in lines


# ------------------------------------------------------------------ open in Finder
def test_open_only_inside_registered_projects(env, monkeypatch):
    opened = []
    monkeypatch.setattr(folders, "open_command", lambda: "/usr/bin/open")
    monkeypatch.setattr(folders.subprocess, "Popen", lambda argv, **kw: opened.append(argv))
    assert post(env, "/api/open", {"path": env.project})[0] == 200
    assert post(env, "/api/open", {"path": str(Path(env.project) / "src")})[0] == 200
    assert opened == [["/usr/bin/open", env.project], ["/usr/bin/open", str(Path(env.project) / "src")]]
    for bad in (str(env.home), "/etc", str(Path(env.project) / ".." / ".."), ""):
        assert post(env, "/api/open", {"path": bad})[0] == 403
    assert post(env, "/api/open", {"path": str(Path(env.project) / "missing")})[0] == 404
    monkeypatch.setattr(folders, "open_command", lambda: None)
    assert post(env, "/api/open", {"path": env.project})[0] == 501


# ------------------------------------------------------------------ tools
def test_git_identity_is_validated_and_saved(env):
    assert get(env, "/api/tools")[1]["git"] == {"name": "", "email": ""}
    for bad in ({"name": "", "email": "a@b.co"}, {"name": "x", "email": "not-an-email"}, {"name": "x" * 61, "email": "a@b.co"}, {"name": "a\nb", "email": "a@b.co"}):
        assert post(env, "/api/tools/git-identity", bad)[0] == 400
    st, body, _ = post(env, "/api/tools/git-identity", {"name": "Dave", "email": "dave@example.com"})
    assert st == 200 and get(env, "/api/tools")[1]["git"] == {"name": "Dave", "email": "dave@example.com"}


def test_tool_jobs_run_without_a_project_and_block_other_jobs(env):
    for tool, needle in (("doctor", "doctor"), ("selftest", "selftest"), ("install-ytdlp", "yt-dlp")):
        sleeper.calls.clear()
        env.app.job = None
        st, body, _ = post(env, "/api/tools/run", {"tool": tool})
        assert st == 200 and body["job"]["kind"] == "tool" and needle in sleeper.calls[-1], tool
    assert post(env, "/api/tools/run", {"tool": "doctor"})[0] == 409  # one job at a time
    assert post(env, "/api/run", {"goal": "x", "project": env.project})[0] == 409
    env.app.job["popen"].kill()
    env.app.job["popen"].wait()
    assert post(env, "/api/tools/run", {"tool": "rm -rf /"})[0] == 400


def test_pip_install_uses_this_interpreter_and_only_yt_dlp(env):
    post(env, "/api/tools/run", {"tool": "install-ytdlp"})
    a = sleeper.calls[-1]
    assert a[0] == os.sys.executable and a[1:] == ["-m", "pip", "install", "--upgrade", "yt-dlp"]


def test_update_and_restart_need_a_git_checkout_started_by_the_scripts(env, monkeypatch):
    env.app.job = None
    monkeypatch.setattr(env.app, "source_root", lambda: None)
    assert post(env, "/api/tools/run", {"tool": "update"})[0] == 501 and post(env, "/api/tools/restart", {})[0] == 501
    root = Path(dashboard.__file__).resolve().parent.parent
    monkeypatch.setattr(env.app, "source_root", lambda: root)
    st, body, _ = post(env, "/api/tools/run", {"tool": "update"})
    assert st == 200 and sleeper.calls[-1] == ["bash", str(root / "scripts" / "update.sh")]
    env.app.job["popen"].kill(); env.app.job["popen"].wait()
    assert post(env, "/api/tools/restart", {})[0] == 501  # not started by start.sh (pid file does not match)
    launched = []
    monkeypatch.setattr(env.app, "started_by_script", lambda: True)
    monkeypatch.setattr(dashboard.subprocess, "Popen", lambda argv, **kw: launched.append((argv, kw)))
    assert post(env, "/api/tools/restart", {})[0] == 200
    assert launched[0][0] == ["bash", str(root / "scripts" / "restart.sh")] and launched[0][1]["start_new_session"] is True


def test_restart_is_refused_while_a_job_runs(env, monkeypatch):
    root = Path(dashboard.__file__).resolve().parent.parent
    monkeypatch.setattr(env.app, "source_root", lambda: root)
    monkeypatch.setattr(env.app, "started_by_script", lambda: True)
    post(env, "/api/analyze", {"project": env.project})
    assert post(env, "/api/tools/restart", {})[0] == 409


def test_the_access_key_survives_a_restart_and_is_kept_private(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLOMON_HOME", str(tmp_path / "h"))
    first = dashboard._persistent_token()
    assert dashboard._persistent_token() == first and len(first) >= 24
    f = tmp_path / "h" / "dashboard.token"
    assert stat.S_IMODE(f.stat().st_mode) == 0o600
    f.write_text("short")  # a damaged file is replaced, not trusted
    assert dashboard._persistent_token() != "short"


# ------------------------------------------------------------------ the scripts
@pytest.mark.parametrize("name", ["update.sh", "restart.sh"])
def test_scripts_are_valid_and_executable(name):
    f = Path(dashboard.__file__).resolve().parent.parent / "scripts" / name
    assert f.stat().st_mode & stat.S_IXUSR
    assert subprocess.run(["bash", "-n", str(f)]).returncode == 0


def test_update_script_refuses_a_folder_that_is_not_a_git_checkout(tmp_path):
    root = Path(dashboard.__file__).resolve().parent.parent
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "update.sh").write_text((root / "scripts" / "update.sh").read_text())
    res = subprocess.run(["bash", str(tmp_path / "scripts" / "update.sh")], capture_output=True, text=True, env=dict(os.environ, HOME=str(tmp_path)))
    assert res.returncode == 1 and "git으로 받은 것이 아니라" in res.stdout


# ------------------------------------------------------------------ make a new folder from the browser
def test_make_a_new_folder_inside_home_and_pick_it(env):
    st, body, _ = post(env, "/api/folders/mkdir", {"parent": str(env.home), "name": "새 프로젝트 폴더"})
    assert st == 200 and Path(body["path"]).is_dir() and Path(body["path"]).name == "새 프로젝트 폴더" and list(Path(body["path"]).iterdir()) == []
    assert "새 프로젝트 폴더" in [d["name"] for d in get(env, "/api/browse")[1]["dirs"]]
    assert post(env, "/api/project", {"path": body["path"]})[0] == 200  # and it can be added as a project right away


@pytest.mark.parametrize("name,code", [("", 400), ("   ", 400), (".hidden", 400), ("a/b", 400), ("a\\b", 400), ("..", 400), ("x" * 81, 400), ("name.", 400),
                                       ("line\nbreak", 400), ("proj", 409)])
def test_new_folder_names_are_validated(env, name, code):
    assert post(env, "/api/folders/mkdir", {"parent": str(env.home), "name": name})[0] == code


@pytest.mark.parametrize("parent", ["/etc", "/tmp", "{home}/..", "{home}/nothing-here", ""])
def test_new_folders_only_inside_home(env, parent):
    st, _, _ = post(env, "/api/folders/mkdir", {"parent": parent.format(home=env.home), "name": "x-new"})
    assert st == 403
    assert not Path("/etc/x-new").exists() and not (env.home.parent / "x-new").exists()


def test_new_folder_needs_the_token(env):
    assert env.call("POST", "/api/folders/mkdir", {"parent": str(env.home), "name": "x"}, token=None)[0] == 401


def test_a_project_added_now_is_on_the_board_immediately(env):
    get(env, "/api/portfolio")  # fills the short-lived cache
    d = env.home / "just-added"
    d.mkdir()
    post(env, "/api/project", {"path": str(d)})
    assert str(d) in [c["path"] for c in get(env, "/api/portfolio")[1]["projects"]]
