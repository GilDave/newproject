"""scripts/start.sh and scripts/stop.sh: a background dashboard with its own start and stop."""
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from test_start_script import ROOT, free_port


@pytest.fixture
def box(tmp_path):
    state = tmp_path / "state"
    env = dict(os.environ, HOME=str(tmp_path / "home"), SOLOMON_HOME=str(state), SOLOMON_PYTHON=sys.executable)
    (tmp_path / "home").mkdir()

    class Box:
        pass

    b = Box()
    b.state, b.env, b.tmp = state, env, tmp_path
    b.pidf, b.urlf = state / "dashboard.pid", state / "dashboard.url"

    def run(script, *args, timeout=60):
        return subprocess.run(["bash", str(ROOT / "scripts" / script), *args], capture_output=True, text=True,
                              env=env, timeout=timeout)

    b.start = lambda *a: run("start.sh", "--no-browser", "--port", str(free_port()), *a)
    b.stop = lambda *a: run("stop.sh", *a)
    yield b
    run("stop.sh")  # never leave a dashboard behind


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    return not (stat.exists() and stat.read_text().split(")")[-1].split()[0] == "Z")


def get(url, token=True):
    req = urllib.request.Request(url.split("?")[0].replace("/", "/", 1) + "api/state" + (f"?t={url.split('t=')[1]}" if token else ""))
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_start_returns_the_terminal_and_leaves_a_working_dashboard(box):
    t0 = time.time()
    res = box.start()
    assert res.returncode == 0, res.stdout + res.stderr
    assert time.time() - t0 < 30 and "대시보드를 시작했습니다" in res.stdout
    pid = int(box.pidf.read_text())
    assert alive(pid)  # the starter has exited, the dashboard did not
    url = box.urlf.read_text().strip()
    assert url.startswith("http://127.0.0.1:") and "?t=" in url and url in res.stdout
    assert get(url) == 200 and get(url, token=False) == 401
    assert oct(box.urlf.stat().st_mode & 0o777) == "0o600"  # the url contains the access key
    assert oct((box.state / "dashboard.log").stat().st_mode & 0o777) == "0o600"


def test_starting_twice_reuses_the_running_dashboard(box):
    box.start()
    first = int(box.pidf.read_text())
    again = box.start()
    assert again.returncode == 0 and "이미 실행 중" in again.stdout and box.urlf.read_text().strip() in again.stdout
    assert int(box.pidf.read_text()) == first and alive(first)


def test_stop_ends_the_dashboard_and_cleans_up(box):
    box.start()
    pid, url = int(box.pidf.read_text()), box.urlf.read_text().strip()
    res = box.stop()
    assert res.returncode == 0 and "종료했습니다" in res.stdout
    assert not alive(pid) and not box.pidf.exists() and not box.urlf.exists()
    with pytest.raises(Exception):
        get(url)  # the port is closed
    assert "실행 중이 아닙니다" in box.stop().stdout  # stopping again is harmless


def test_a_stale_pid_file_does_not_block_a_new_start(box):
    box.state.mkdir(parents=True, exist_ok=True)
    box.pidf.write_text("999999")
    res = box.start()
    assert res.returncode == 0 and alive(int(box.pidf.read_text())) and int(box.pidf.read_text()) != 999999


def test_stop_never_kills_an_unrelated_process_that_reused_the_pid(box):
    victim = subprocess.Popen(["sleep", "60"])
    try:
        box.state.mkdir(parents=True, exist_ok=True)
        box.pidf.write_text(str(victim.pid))
        assert "실행 중이 아닙니다" in box.stop().stdout
        assert victim.poll() is None  # still running
        res = box.start()  # and a start is not fooled by it either
        assert res.returncode == 0 and int(box.pidf.read_text()) != victim.pid
    finally:
        victim.kill()
        victim.wait()


def test_a_failed_start_shows_the_log_and_leaves_no_pid_file(box):
    res = subprocess.run(["bash", str(ROOT / "scripts" / "start.sh"), "--no-browser", "--port", "not-a-number"],
                         capture_output=True, text=True, env=box.env, timeout=60)
    assert res.returncode == 1 and "시작하지 못했습니다" in res.stdout and "invalid int value" in res.stdout
    assert not box.pidf.exists()


def test_stop_with_runs_flag_is_accepted_when_nothing_is_running(box):
    box.start()
    res = box.stop("--with-runs")
    assert res.returncode == 0 and "종료했습니다" in res.stdout


def _projects(box):
    import json as _json

    url = box.urlf.read_text().strip()
    req = urllib.request.Request(url.split("?")[0] + "api/state?t=" + url.split("t=")[1])
    with urllib.request.urlopen(req, timeout=5) as r:
        return _json.load(r)["projects"]


def test_project_given_to_an_already_running_dashboard_is_registered_not_ignored(box):
    from helpers import make_repo

    box.start()
    other = make_repo(box.tmp / "other")
    res = box.start("--project", str(other))
    assert res.returncode == 0 and "이미 실행 중" in res.stdout and "프로젝트를 추가했습니다" in res.stdout
    assert str(other.resolve()) in _projects(box)


def test_a_missing_project_for_a_running_dashboard_says_so(box):
    box.start()
    missing = box.tmp / "missing"
    res = box.start("--project", str(missing))
    assert "프로젝트를 추가하지 못했습니다" in res.stdout and str(missing) not in "".join(_projects(box))


def test_a_plain_folder_without_git_is_accepted_for_a_running_dashboard(box):
    box.start()
    plain = box.tmp / "plain"
    plain.mkdir()
    res = box.start("--project", str(plain))
    assert "프로젝트를 추가했습니다" in res.stdout and str(plain.resolve()) in _projects(box)


def test_options_that_only_apply_at_startup_are_reported_as_ignored(box):
    box.start()
    res = box.start("--port", "9999")
    assert "적용되지 않았습니다" in res.stdout and "--port" in res.stdout
