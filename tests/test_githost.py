import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import quote

import pytest

from solomon import githost, gitwatch
from solomon.settings import Settings

from test_dashboard import Env, sleeper
from test_gitwatch import repo, sh

TOKEN = "ghp_" + "A1b2C3d4E5" * 3


class FakeGitHub:
    """A tiny local stand-in for api.github.com."""

    def __init__(self, tmp_path):
        self.tmp, self.seen, self.taken, self.mode = tmp_path, [], set(), "ok"
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, headers=None):
                raw = json.dumps(body).encode()
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                outer.seen.append((self.command, self.path, self.headers.get("Authorization")))
                if outer.mode == "boom":
                    return self._send(500, {"message": "oops " + (self.headers.get("Authorization") or "")})
                if self.headers.get("Authorization") != "Bearer " + TOKEN:
                    return self._send(401, {"message": "Bad credentials"})
                self._send(200, {"login": "dave"}, {"X-OAuth-Scopes": "repo"})

            def do_POST(self):
                outer.seen.append((self.command, self.path, self.headers.get("Authorization")))
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                if self.headers.get("Authorization") != "Bearer " + TOKEN:
                    return self._send(401, {})
                if outer.mode == "forbidden":
                    return self._send(403, {})
                if body["name"] in outer.taken:
                    return self._send(422, {"message": "name already exists"})
                outer.taken.add(body["name"])
                bare = outer.tmp / (body["name"] + ".git")
                subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
                outer.last = body
                self._send(201, {"clone_url": str(bare), "html_url": "https://github.com/dave/" + body["name"], "private": body["private"]})

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def gh(tmp_path, monkeypatch):
    store = tmp_path / "creds"
    cfg = tmp_path / "gitconfig"
    cfg.write_text(f"[user]\n\tname = T\n\temail = t@x.com\n[credential]\n\thelper = store --file={store}\n")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    monkeypatch.setenv("SOLOMON_HOME", str(tmp_path / "solhome"))
    fake = FakeGitHub(tmp_path)
    monkeypatch.setattr(githost, "API", fake.url)
    fake.store = store
    yield fake
    fake.close()


def login():
    return githost.save_login(TOKEN)


def test_login_rejects_bad_format_and_bad_tokens_without_leaking(gh):
    for bad in ("", "short", "has space " * 5, "ghp_" + "x" * 30 + "\n\n$(rm)"):
        with pytest.raises(githost.GithubError) as e:
            githost.save_login(bad)
        assert e.value.status == 400
    with pytest.raises(githost.GithubError) as e:
        githost.save_login("ghp_" + "Z" * 36)
    assert e.value.status == 401 and "Z" * 20 not in e.value.message
    gh.mode = "boom"
    with pytest.raises(githost.GithubError) as e:
        githost.save_login(TOKEN)
    assert TOKEN not in e.value.message and githost.saved_login() == ""


def test_login_stores_the_token_only_in_the_credential_helper(gh, tmp_path):
    r = login()
    assert r["login"] == "dave" and r["scopes"] == "repo" and TOKEN not in json.dumps(r)
    assert githost.saved_login() == "dave" and githost.status()["login"] == "dave"
    assert TOKEN not in (tmp_path / "solhome" / "github.json").read_text()
    assert TOKEN in gh.store.read_text()                         # git 의 자격 증명 저장소(맥에서는 키체인)에 있다
    out = githost.logout()
    assert githost.saved_login() == "" and TOKEN not in gh.store.read_text() and out["ok"]


def test_create_repo_is_private_sets_origin_and_refuses_duplicates(gh, tmp_path):
    work = repo(tmp_path / "work")
    with pytest.raises(githost.GithubError) as e:
        githost.create_repo(work, "app", "")
    assert e.value.status == 401                                 # 로그인 전
    login()
    for bad in ("", "-x", "a b", "x" * 101, "name.git"):
        with pytest.raises(githost.GithubError) as e:
            githost.create_repo(work, bad, "")
        assert e.value.status == 400
    res = githost.create_repo(work, "teamsched", "팀 일정")
    assert res["private"] is True and gh.last["private"] is True and gh.last["auto_init"] is False
    assert [r["name"] for r in gitwatch.inspect(work)["remotes"]] == ["origin"]
    assert ("POST", "/user/repos", "Bearer " + TOKEN) in gh.seen and TOKEN not in json.dumps(res)
    with pytest.raises(githost.GithubError) as e:
        githost.create_repo(work, "other", "")
    assert e.value.status == 409                                 # 이미 origin 이 있음
    work2 = repo(tmp_path / "work2")
    with pytest.raises(githost.GithubError) as e:
        githost.create_repo(work2, "teamsched", "")
    assert e.value.status == 409
    work3 = repo(tmp_path / "work3")
    gh.mode = "forbidden"
    with pytest.raises(githost.GithubError) as e:
        githost.create_repo(work3, "zzz", "")
    assert e.value.status == 403


def test_push_preview_lists_commits_and_blocks_unsafe_pushes(gh, tmp_path):
    work = repo(tmp_path / "work")
    pre = githost.push_preview(work)
    assert pre["blockers"] and "origin" in pre["blockers"][0]
    login()
    githost.create_repo(work, "proj", "")
    pre = githost.push_preview(work)
    assert pre["blockers"] == [] and pre["first_push"] and pre["count"] == 1 and pre["commits"][0]["subject"] == "first"
    with pytest.raises(githost.GithubError) as e:
        githost.push(work, False)
    assert e.value.status == 400
    (work / ".env").write_text("KEY=1\n")
    sh(work, "add", ".env")
    sh(work, "commit", "-qm", "oops")
    pre = githost.push_preview(work)
    assert any("비밀" in b for b in pre["blockers"])
    with pytest.raises(githost.GithubError) as e:
        githost.push(work, True)
    assert e.value.status == 409 and not (tmp_path / "proj.git" / "refs" / "heads" / "main").exists()


def test_push_sends_the_branch_and_then_has_nothing_left_to_push(gh, tmp_path):
    work = repo(tmp_path / "work")
    login()
    githost.create_repo(work, "proj", "")
    res = githost.push(work, True)
    assert res["ok"] and "1개" in res["message"]
    head = subprocess.run(["git", "rev-parse", "main"], cwd=tmp_path / "proj.git", capture_output=True, text=True).stdout.strip()
    assert head == subprocess.run(["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True).stdout.strip()
    info = gitwatch.inspect(work)
    assert info["upstream"] == "origin/main" and (info["ahead"], info["behind"]) == (0, 0)
    assert any("새 커밋이 없습니다" in b for b in githost.push_preview(work)["blockers"])
    (work / "b.txt").write_text("b")
    sh(work, "add", "-A")
    sh(work, "commit", "-qm", "second")
    pre = githost.push_preview(work)
    assert pre["blockers"] == [] and pre["count"] == 1 and pre["commits"][0]["subject"] == "second" and not pre["first_push"]
    assert githost.push(work, True)["ok"]


def test_push_refuses_when_the_remote_is_ahead_or_head_is_detached(gh, tmp_path):
    work = repo(tmp_path / "work")
    login()
    githost.create_repo(work, "proj", "")
    githost.push(work, True)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(tmp_path / "proj.git"), str(other)], check=True)
    (other / "z").write_text("z")
    sh(other, "add", "-A")
    sh(other, "commit", "-qm", "remote")
    sh(other, "push", "-q")
    (work / "c").write_text("c")
    sh(work, "add", "-A")
    sh(work, "commit", "-qm", "local")
    gitwatch.fetch(work)
    pre = githost.push_preview(work)
    assert any("받지 않은 커밋" in b for b in pre["blockers"])
    with pytest.raises(githost.GithubError):
        githost.push(work, True)                                 # --force 로 덮어쓰지 않는다
    assert subprocess.run(["git", "log", "--format=%s", "main"], cwd=tmp_path / "proj.git", capture_output=True, text=True).stdout.split() == ["remote", "first"]
    sh(work, "checkout", "-q", "--detach")
    assert any("detached" in b for b in githost.push_preview(work)["blockers"])


# ------------------------------------------------------------------ dashboard API
@pytest.fixture
def env(tmp_path, gh):
    sleeper.calls.clear()
    e = Env(tmp_path, settings=Settings(projects_root=str(tmp_path / "Solomon-Projects")))
    yield e
    e.close()


def test_api_flow_login_create_preview_push(env, gh, tmp_path):
    proj = env.project
    st, b, _ = env.call("GET", "/api/github/status")
    assert st == 200 and b["login"] == "" and "identity" in b
    assert env.call("GET", "/api/github/status", token="bad")[0] == 401
    assert env.call("POST", "/api/github/login", {"token": "nope"})[0] == 400
    st, b, _ = env.call("POST", "/api/github/login", {"token": TOKEN})
    assert st == 200 and b["login"] == "dave" and TOKEN not in json.dumps(b)
    st, b, _ = env.call("POST", "/api/git/create-repo", {"path": proj, "name": "proj-x", "description": "t"})
    assert st == 200 and b["private"] is True
    assert env.call("POST", "/api/git/create-repo", {"path": "/x", "name": "a"})[0] == 400
    assert env.call("POST", "/api/git/create-repo", {"path": proj, "name": "again"})[0] == 409
    st, b, _ = env.call("GET", "/api/git/push-preview?project=" + quote(proj))
    assert st == 200 and b["blockers"] == [] and b["count"] >= 1
    assert env.call("POST", "/api/git/push", {"path": proj})[0] == 400                   # confirm 없이는 거절
    assert env.call("POST", "/api/git/push", {"path": proj, "confirm": "yes"})[0] == 400  # true 만 허용
    st, b, _ = env.call("POST", "/api/git/push", {"path": proj, "confirm": True})
    assert st == 200 and b["ok"]
    assert env.call("POST", "/api/github/login", {"token": TOKEN}, token="bad")[0] == 401
    assert env.call("POST", "/api/github/logout", {})[0] == 200
    assert env.call("GET", "/api/github/status")[1]["login"] == ""
