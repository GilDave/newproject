import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest

from solomon import gitwatch
from solomon.settings import Settings

from test_dashboard import Env, sleeper


def sh(cwd, *args):
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@x", *args], cwd=cwd, check=True, capture_output=True)


def repo(path: Path, files=None):
    path.mkdir(parents=True, exist_ok=True)
    sh(path, "init", "-q", "-b", "main")
    for n, c in (files or {"a.txt": "1\n"}).items():
        (path / n).parent.mkdir(parents=True, exist_ok=True)
        (path / n).write_text(c)
    sh(path, "add", "-A")
    sh(path, "commit", "-qm", "first")
    return path


def with_remote(tmp_path):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    work = repo(tmp_path / "work")
    sh(work, "remote", "add", "origin", str(bare))
    sh(work, "push", "-q", "-u", "origin", "main")
    return work, bare


def codes(info):
    return {w["code"] for w in info["warnings"]}


def test_states_missing_none_empty_ready(tmp_path):
    assert gitwatch.inspect(tmp_path / "nope")["state"] == "missing"
    plain = tmp_path / "plain"
    plain.mkdir()
    assert gitwatch.inspect(plain)["state"] == "none"
    empty = tmp_path / "empty"
    empty.mkdir()
    sh(empty, "init", "-q")
    i = gitwatch.inspect(empty)
    assert i["state"] == "empty" and "empty" in codes(i)
    ready = gitwatch.inspect(repo(tmp_path / "r"))
    assert ready["state"] == "ready" and ready["branch"] == "main" and ready["last_commit"]["subject"] == "first" and ready["stash"] == 0


def test_no_remote_warns_and_clean_synced_repo_has_no_warnings(tmp_path):
    i = gitwatch.inspect(repo(tmp_path / "r"))
    assert "no_remote" in codes(i) and i["remotes"] == []
    work, _ = with_remote(tmp_path)
    j = gitwatch.inspect(work)
    assert j["upstream"] == "origin/main" and (j["ahead"], j["behind"]) == (0, 0) and j["warnings"] == [] and j["changed"] == 0


def test_ahead_dirty_untracked_and_behind_are_counted(tmp_path):
    work, bare = with_remote(tmp_path)
    (work / "b.txt").write_text("x")                      # untracked
    (work / "a.txt").write_text("changed\n")              # modified
    (work / "c.txt").write_text("c")
    sh(work, "add", "c.txt")
    sh(work, "commit", "-qm", "local")                    # one unpushed commit
    i = gitwatch.inspect(work)
    assert (i["ahead"], i["behind"], i["changed"], i["untracked"]) == (1, 0, 1, 1) and {"ahead"} <= codes(i)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True)
    (other / "z.txt").write_text("z")
    sh(other, "add", "-A")
    sh(other, "commit", "-qm", "remote work")
    sh(other, "push", "-q")
    assert gitwatch.inspect(work)["behind"] == 0           # not known until fetched
    assert gitwatch.fetch(work)["ok"]
    j = gitwatch.inspect(work)
    assert (j["ahead"], j["behind"]) == (1, 1) and "behind" in codes(j) and j["fetched_at"]


def test_tracked_secret_is_flagged_but_env_example_is_not(tmp_path):
    i = gitwatch.inspect(repo(tmp_path / "r", {".env": "KEY=1\n", "app.py": "x\n", ".env.example": "KEY=\n", "sub/server.key": "k\n"}))
    assert "secret" in codes(i) and set(i["tracked_secrets"]) == {".env", "sub/server.key"} and i["warnings"][0]["level"] == "bad"
    assert "secret" not in codes(gitwatch.inspect(repo(tmp_path / "r2", {".env.example": "KEY=\n"})))


def test_subfolder_of_a_larger_repository_is_reported_as_nested(tmp_path):
    top = repo(tmp_path / "mono", {"pkg/a.py": "1\n"})
    i = gitwatch.inspect(top / "pkg")
    assert i["nested"] is True and Path(i["git_root"]).resolve() == top.resolve() and "nested" in codes(i)


def test_detached_head_and_stash_and_worktree(tmp_path):
    work = repo(tmp_path / "r")
    (work / "a.txt").write_text("dirty\n")
    sh(work, "stash")
    sh(work, "worktree", "add", "-q", str(tmp_path / "wt"))
    sh(work, "checkout", "-q", "--detach")
    i = gitwatch.inspect(work)
    assert i["detached"] and i["stash"] == 1 and i["worktrees"] == 1 and "detached" in codes(i)
    assert gitwatch.inspect(tmp_path / "wt")["linked_worktree"] is True


@pytest.mark.parametrize("url,ok", [
    ("https://github.com/GilDave/newproject.git", True), ("https://github.com/GilDave/newproject", True), ("git@github.com:GilDave/newproject.git", True),
    ("ssh://git@host.example:2222/team/repo.git", True), ("/srv/git/repo.git", True),
    ("", False), ("-oProxyCommand=evil", False), ("ext::sh -c touch /tmp/x", False), ("https://x.com/a b", False), ("https://x.com/a;rm -rf", False),
    ("file:///etc/passwd", False), ("/srv/../etc", False), ("https://x.com/a\nb", False), ("git@host:a\"b", False)])
def test_remote_url_validation(url, ok):
    assert gitwatch.valid_remote_url(url) is ok


def test_urls_are_masked_when_they_carry_a_token():
    assert gitwatch.mask_url("https://user:ghp_secret@github.com/a/b.git") == "https://***@github.com/a/b.git"
    assert gitwatch.mask_url("git@github.com:a/b.git") == "git@github.com:a/b.git"


def test_set_remote_adds_then_changes_and_rejects_bad_input(tmp_path):
    work = repo(tmp_path / "r")
    assert "추가" in gitwatch.set_remote(work, "origin", "https://github.com/a/b.git")["message"]
    assert "바꿨" in gitwatch.set_remote(work, "origin", "https://github.com/a/c.git")["message"]
    assert [r["fetch"] for r in gitwatch.inspect(work)["remotes"]] == ["https://github.com/a/c.git"]
    for name, url in (("--bad", "https://github.com/a/b.git"), ("origin", "ext::sh -c x"), ("", "https://github.com/a/b.git")):
        with pytest.raises(gitwatch.GitWatchError) as e:
            gitwatch.set_remote(work, name, url)
        assert e.value.status == 400
    with pytest.raises(gitwatch.GitWatchError):
        gitwatch.set_remote(tmp_path / "plain", "origin", "https://github.com/a/b.git")


def test_fetch_errors_are_readable_and_do_not_touch_files(tmp_path):
    work = repo(tmp_path / "r")
    with pytest.raises(gitwatch.GitWatchError) as e:
        gitwatch.fetch(work)
    assert e.value.status == 400
    sh(work, "remote", "add", "origin", str(tmp_path / "missing.git"))
    (work / "a.txt").write_text("keep me\n")
    with pytest.raises(gitwatch.GitWatchError) as e:
        gitwatch.fetch(work)
    assert e.value.status == 502 and (work / "a.txt").read_text() == "keep me\n"
    with pytest.raises(gitwatch.GitWatchError):
        gitwatch.fetch(work, "nope")


def test_summary_counts(tmp_path):
    work, _ = with_remote(tmp_path)
    (work / "x").write_text("1")
    plain = tmp_path / "plain"
    plain.mkdir()
    s = gitwatch.summarize([gitwatch.inspect(work), gitwatch.inspect(repo(tmp_path / "solo")), gitwatch.inspect(plain)])
    assert s == {"projects": 3, "no_git": 1, "no_remote": 1, "dirty": 1, "unpushed": 0, "behind": 0, "bad": 0}


# ------------------------------------------------------------------ dashboard API
@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SOLOMON_HOME", str(tmp_path / "solhome"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    sleeper.calls.clear()
    e = Env(tmp_path, settings=Settings(projects_root=str(tmp_path / "Solomon-Projects")))
    yield e
    e.close()


def test_api_overview_fetch_and_remote(env, tmp_path):
    st, b, _ = env.call("GET", "/api/git")
    assert st == 200 and b["summary"]["projects"] == 1 and b["projects"][0]["state"] == "ready" and "no_remote" in {w["code"] for w in b["projects"][0]["warnings"]}
    proj = env.project
    assert env.call("GET", "/api/git?project=" + quote("/not/registered"))[0] == 400
    assert env.call("GET", "/api/git", token="bad")[0] == 401
    st, r, _ = env.call("POST", "/api/git/remote", {"path": proj, "name": "origin", "url": "https://github.com/a/b.git"})
    assert st == 200 and "추가" in r["message"]
    st, b, _ = env.call("GET", "/api/git?project=" + quote(proj))
    assert b["projects"][0]["remotes"][0]["fetch"] == "https://github.com/a/b.git" and "no_remote" not in {w["code"] for w in b["projects"][0]["warnings"]}
    assert env.call("POST", "/api/git/remote", {"path": proj, "name": "origin", "url": "ext::sh -c x"})[0] == 400
    assert env.call("POST", "/api/git/remote", {"path": "/x", "name": "origin", "url": "https://github.com/a/b.git"})[0] == 400
    assert env.call("POST", "/api/git/fetch", {"path": "/x"})[0] == 400
    assert env.call("POST", "/api/git/remote", {"path": proj, "name": "origin", "url": "https://github.com/a/b.git"}, token="bad")[0] == 401
