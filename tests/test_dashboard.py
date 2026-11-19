import http.client
import json
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from solomon.dashboard import MAX_BODY, MAX_GOAL, DashboardApp, make_handler
from solomon.orchestrator import Orchestrator
from solomon.settings import Settings

from helpers import ScriptedProvider, make_repo, make_settings, order_json, review_json, write_file

TOKEN = "test-token-123"


def sleeper(args, log_path):
    """Stands in for `solomon run`: a long-lived process in its own session, like the real launcher."""
    sleeper.calls.append(args)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True,
                            stdout=open(log_path, "w"), stderr=subprocess.STDOUT)


sleeper.calls = []


def crasher(args, log_path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([sys.executable, "-c", "print('boom: bad config'); raise SystemExit(2)"],
                            stdout=open(log_path, "w"), stderr=subprocess.STDOUT)


class Env:
    def __init__(self, tmp_path, launcher=sleeper, settings=None):
        self.repo = make_repo(tmp_path)
        self.app = DashboardApp(settings or Settings(), [str(self.repo)], store_path=tmp_path / "store.json", launcher=launcher)
        hosts: set = set()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.app, TOKEN, hosts))
        self.port = self.server.server_address[1]
        hosts.update({f"127.0.0.1:{self.port}", f"localhost:{self.port}"})
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.project = str(self.repo.resolve())

    def call(self, method, path, body=None, token=TOKEN, host=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = dict(headers or {})
        if token is not None:
            h["X-Solomon-Token"] = token
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host or f"127.0.0.1:{self.port}")
        payload = json.dumps(body).encode() if body is not None else b""
        if method == "POST":
            h.setdefault("Content-Type", "application/json")
            h["Content-Length"] = str(len(payload))
        for k, v in h.items():
            conn.putheader(k, v)
        conn.endheaders(payload)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        try:
            data = json.loads(raw)
        except ValueError:
            data = raw
        return resp.status, data, resp

    def state(self, **q):
        qs = "&".join(f"{k}={v}" for k, v in q.items())
        return self.call("GET", "/api/state" + ("?" + qs if qs else ""))

    def finish_a_run(self):
        claude = ScriptedProvider("claude", [order_json(), review_json()])
        codex = ScriptedProvider("codex", [write_file("src/new.py")])
        return Orchestrator(make_settings(), {"claude": claude, "codex": codex}).run("add new module", self.repo)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        job = self.app.job
        if job and job["popen"].poll() is None:
            job["popen"].kill()


@pytest.fixture
def env(tmp_path):
    sleeper.calls.clear()
    e = Env(tmp_path)
    yield e
    e.close()


# ------------------------------------------------------------- security
def test_api_requires_the_token(env):
    assert env.call("GET", "/api/state", token=None)[0] == 401
    assert env.call("GET", "/api/state", token="wrong")[0] == 401
    assert env.call("POST", "/api/stop", {}, token=None)[0] == 401
    assert env.state()[0] == 200


def test_token_can_also_come_from_the_query_string_for_get(env):
    assert env.call("GET", f"/api/state?t={TOKEN}", token=None)[0] == 200


def test_foreign_host_header_is_rejected(env):
    assert env.call("GET", "/", host="evil.example.com")[0] == 403
    assert env.call("GET", "/api/state", host="evil.example.com")[0] == 403


def test_foreign_origin_is_rejected_on_post(env):
    status, _, _ = env.call("POST", "/api/stop", {}, headers={"Origin": "http://evil.example.com"})
    assert status == 403


def test_oversized_and_malformed_bodies_are_rejected(env):
    assert env.call("POST", "/api/run", {"goal": "x" * (MAX_BODY + 1), "project": env.project})[0] == 413
    conn = http.client.HTTPConnection("127.0.0.1", env.port, timeout=10)
    conn.request("POST", "/api/run", body=b"not json", headers={"X-Solomon-Token": TOKEN, "Content-Length": "8"})
    assert conn.getresponse().status == 400


def test_page_is_served_without_data_and_with_a_strict_csp(env):
    status, body, resp = env.call("GET", "/", token=None)
    assert status == 200 and b"Solomon" in body
    csp = resp.getheader("Content-Security-Policy")
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp
    assert env.project.encode() not in body  # the page itself carries no project data


def test_unknown_route_is_404(env):
    assert env.call("GET", "/etc/passwd")[0] == 404
    assert env.call("POST", "/api/nope", {})[0] == 404


# ---------------------------------------------------------------- state
def test_state_for_a_finished_run(env):
    env.finish_a_run()
    status, st, _ = env.state()
    assert status == 200 and st["project"] == env.project
    cur = st["current"]
    assert cur["status"] == "PASSED" and cur["finished"] and not cur["live"] and not cur["interrupted"]
    assert cur["stage_key"] == "done" and cur["roles"]["builder"] == "codex"
    assert {c["role"] for c in cur["calls"]} == {"planner", "builder", "reviewer"}
    assert set(cur["providers"]) == {"claude", "codex"}
    assert st["apply"]["possible"] is True and st["runs"][0]["status"] == "PASSED"
    assert [p["name"] for p in st["providers"]] == ["claude", "codex", "antigravity"]


def test_unfinished_run_without_a_job_is_reported_as_interrupted(env):
    run_dir = Path(env.project) / ".solomon" / "runs" / "20261118-100000-abcdef"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(json.dumps({"ts": "2026-11-18T10:00:00", "event": "run_start", "goal": "g"}) + "\n")
    cur = env.state()[1]["current"]
    assert cur["interrupted"] is True and cur["live"] is False


def test_live_run_shows_who_is_working(env):
    status, body, _ = env.call("POST", "/api/run", {"goal": "do a thing", "project": env.project})
    assert status == 200 and body["job"]["state"] == "running"
    run_dir = Path(env.project) / ".solomon" / "runs" / "20261118-100000-abcdef"
    run_dir.mkdir(parents=True)
    events = [{"ts": "2026-11-18T10:00:00", "event": "run_start", "goal": "g", "roles": {"planner": "claude", "builder": "codex", "reviewer": "claude"}},
              {"ts": "2026-11-18T10:00:01", "event": "call_start", "role": "builder", "provider": "codex", "prompt_chars": 5}]
    (run_dir / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    cur = env.state()[1]["current"]
    assert cur["live"] is True and cur["active"]["role"] == "builder" and cur["active"]["provider"] == "codex"
    assert cur["stage_key"] == "build"


def test_unknown_run_id_falls_back_to_the_latest(env):
    env.finish_a_run()
    assert env.state(run="../../etc")[1]["current"]["status"] == "PASSED"


# ---------------------------------------------------------- run and stop
def test_start_stop_and_single_job(env):
    status, body, _ = env.call("POST", "/api/run", {"goal": "fix the crash", "project": env.project})
    assert status == 200
    args = sleeper.calls[-1]
    assert args[1:4] == ["-m", "solomon", "run"] and args[4] == "fix the crash" and env.project in args
    assert env.call("POST", "/api/run", {"goal": "second", "project": env.project})[0] == 409  # one at a time
    assert env.state()[1]["job"]["state"] == "running"
    assert env.call("POST", "/api/stop", {})[0] == 200
    deadline = time.time() + 8
    while time.time() < deadline and env.state()[1]["job"]["state"] == "running":
        time.sleep(0.2)
    assert env.state()[1]["job"]["state"] == "exited"
    assert env.call("POST", "/api/stop", {})[0] == 409  # nothing left to stop


def test_run_validation(env, tmp_path):
    assert env.call("POST", "/api/run", {"goal": "   ", "project": env.project})[0] == 400
    assert env.call("POST", "/api/run", {"goal": "x" * (MAX_GOAL + 1), "project": env.project})[0] == 400
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    status, body, _ = env.call("POST", "/api/run", {"goal": "ok", "project": str(plain)})
    assert status == 400 and "git" in body["error"].lower()
    assert env.call("POST", "/api/run", {"goal": "ok", "project": "/definitely/missing"})[0] == 400
    assert not sleeper.calls  # nothing was launched


def test_a_job_that_dies_before_creating_a_run_shows_its_log(tmp_path):
    e = Env(tmp_path, launcher=crasher)
    try:
        assert e.call("POST", "/api/run", {"goal": "ok", "project": e.project})[0] == 200
        deadline = time.time() + 8
        job = {}
        while time.time() < deadline:
            job = e.state()[1]["job"]
            if job["state"] == "exited":
                break
            time.sleep(0.2)
        assert job["state"] == "exited" and job["exit_code"] == 2 and job["orphan"] is True
        assert "boom: bad config" in job["log_tail"]
    finally:
        e.close()


# ---------------------------------------------------------------- apply
def test_apply_changes_the_original_only_when_asked_and_only_once(env):
    result = env.finish_a_run()
    assert not (env.repo / "src" / "new.py").exists()  # untouched until apply
    status, body, _ = env.call("POST", "/api/apply", {"project": env.project, "run_id": result.run_id})
    assert status == 200 and (env.repo / "src" / "new.py").exists()
    assert "커밋은 하지 않았습니다" in body["message"]
    assert env.call("POST", "/api/apply", {"project": env.project, "run_id": result.run_id})[0] == 409
    assert env.state()[1]["apply"] == {"possible": False, "applied": True, "patch": result.patch_path}


def test_apply_rejects_bad_input(env):
    assert env.call("POST", "/api/apply", {"project": env.project, "run_id": "../../x"})[0] == 400
    assert env.call("POST", "/api/apply", {"project": env.project, "run_id": "20261118-100000-abcdef"})[0] == 404
    assert env.call("POST", "/api/apply", {"project": "/not/known", "run_id": "20261118-100000-abcdef"})[0] == 400


def test_apply_conflict_is_reported_not_forced(env):
    result = env.finish_a_run()
    (env.repo / "src").mkdir(exist_ok=True)
    (env.repo / "src" / "new.py").write_text("someone else wrote this\n")  # the original changed meanwhile
    status, body, _ = env.call("POST", "/api/apply", {"project": env.project, "run_id": result.run_id})
    assert status == 409 and (env.repo / "src" / "new.py").read_text() == "someone else wrote this\n"


# --------------------------------------------------- per-project settings
def test_per_project_test_command(env):
    set_cmd = lambda c: env.call("POST", "/api/test-command", {"project": env.project, "command": c})
    assert env.state()[1]["test_command"] == {"text": "python3 -m pytest -q", "source": "기본 설정"}
    assert set_cmd("npm test")[0] == 200
    assert env.state()[1]["test_command"] == {"text": "npm test", "source": "프로젝트별 설정"}
    env.call("POST", "/api/run", {"goal": "go", "project": env.project})
    assert sleeper.calls[-1][-2:] == ["--test-command", "npm test"]
    assert set_cmd("rm -rf /")[0] == 400  # only approved commands
    assert set_cmd("없음")[0] == 200 and env.state()[1]["test_command"]["text"] == "없음"
    assert set_cmd("")[0] == 200 and env.state()[1]["test_command"]["source"] == "기본 설정"


def test_projects_are_remembered_between_launches(tmp_path):
    e = Env(tmp_path)
    try:
        e.call("POST", "/api/test-command", {"project": e.project, "command": "npm test"})
    finally:
        e.close()
    again = DashboardApp(Settings(), [], store_path=tmp_path / "store.json")
    assert e.project in again.build_state(None, None)["projects"]
    assert again.effective_test_command(e.project)[0] == ["npm", "test"]


def test_any_existing_folder_can_be_added_but_a_missing_one_cannot(env, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert env.call("POST", "/api/project", {"path": str(plain)})[0] == 200  # fine for analysis
    st = env.state(project=str(plain.resolve()))[1]
    assert st["project"] == str(plain.resolve()) and st["project_git"] is False
    assert env.state(project=env.project)[1]["project_git"] is True
    assert env.call("POST", "/api/project", {"path": str(tmp_path / "missing")})[0] == 400
    assert env.call("POST", "/api/project", {"path": env.project})[0] == 200
