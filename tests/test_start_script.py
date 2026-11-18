import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from helpers import make_repo

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(args, tmp_path, env_extra=None):
    env = dict(os.environ, HOME=str(tmp_path / "home"), SOLOMON_PYTHON=sys.executable, **(env_extra or {}))
    (tmp_path / "home").mkdir(exist_ok=True)
    return subprocess.Popen(["bash", str(ROOT / "scripts" / "start_dashboard.sh"), *args], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, env=env, start_new_session=True)


def read_until(proc, needle, seconds=15):
    out, end = "", time.time() + seconds
    while time.time() < end:
        line = proc.stdout.readline()
        if not line:
            break
        out += line
        if needle in line:
            return out
    return out


def stop(proc):
    if proc.poll() is None:
        os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)


def test_one_command_starts_the_dashboard_and_reports_the_ai_clis(tmp_path):
    repo = make_repo(tmp_path)
    port = free_port()
    proc = start(["--project", str(repo), "--port", str(port), "--no-open"], tmp_path)
    try:
        out = read_until(proc, "접속 키")
        assert "AI CLIs" in out and re.search(r"\[(ok|missing)\s*\]\s+claude", out)
        m = re.search(r"http://127\.0\.0\.1:(\d+)/\?t=\S+", out)
        assert m and int(m.group(1)) == port
    finally:
        stop(proc)


def test_pull_flag_is_consumed_by_the_script_and_not_passed_on(tmp_path):
    repo = make_repo(tmp_path)
    proc = start(["--pull", "--project", str(repo), "--port", str(free_port()), "--no-open"], tmp_path)
    try:
        out = read_until(proc, "접속 키")
        assert "== git pull ==" in out and "unrecognized arguments" not in out and "http://127.0.0.1:" in out
    finally:
        stop(proc)


def test_missing_setup_gives_a_clear_message(tmp_path):
    fake_root = tmp_path / "root"
    (fake_root / "scripts").mkdir(parents=True)
    (fake_root / "scripts" / "start_dashboard.sh").write_text((ROOT / "scripts" / "start_dashboard.sh").read_text())
    env = dict(os.environ, HOME=str(tmp_path), PATH="/usr/bin:/bin")
    env.pop("SOLOMON_PYTHON", None)
    res = subprocess.run(["bash", str(fake_root / "scripts" / "start_dashboard.sh")], capture_output=True, text=True, env=env, timeout=30)
    assert res.returncode == 1 and "setup_mac.sh" in res.stdout


def test_double_click_launcher_is_executable_and_wraps_the_script():
    cmd = ROOT / "Start-Solomon.command"
    assert os.access(cmd, os.X_OK) and os.access(ROOT / "scripts" / "start_dashboard.sh", os.X_OK)
    assert "scripts/start_dashboard.sh" in cmd.read_text()
