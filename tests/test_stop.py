"""The stop button end to end: a real `solomon run` process, a fake CLI that never answers."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from helpers import make_repo


from helpers import pid_alive as alive  # works on Linux and macOS (no /proc on a Mac)


def wait_for(cond, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_sigterm_ends_the_run_as_stopped_and_leaves_no_ai_process_behind(tmp_path):
    repo = make_repo(tmp_path)
    pidfile = tmp_path / "fake.pid"
    fake = tmp_path / "fakecli"
    fake.write_text(f"#!{sys.executable}\nimport os, sys, time\nopen({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
                    "sys.stdin.read()\ntime.sleep(300)\n")
    fake.chmod(0o755)
    cfg = tmp_path / "solomon.toml"
    cfg.write_text(f'[executables]\nclaude = "{fake}"\ncodex = "{fake}"\n[timeouts]\nplanner = 120\nno_output = 120\n')

    proc = subprocess.Popen(
        [sys.executable, "-m", "solomon", "run", "do something", "--project", str(repo), "--config", str(cfg)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    try:
        events = lambda: list((repo / ".solomon" / "runs").glob("*/events.jsonl"))
        assert wait_for(lambda: pidfile.exists() and events() and "call_start" in events()[0].read_text())
        fake_pid = int(pidfile.read_text())
        assert alive(fake_pid)

        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)  # exactly what the dashboard's stop button does
        out, _ = proc.communicate(timeout=20)

        assert proc.returncode == 130, out
        assert wait_for(lambda: not alive(fake_pid)), "the fake CLI was left running"
        run_dir = events()[0].parent
        kinds = [json.loads(l)["event"] for l in (run_dir / "events.jsonl").read_text().splitlines()]
        assert kinds[-1] == "run_end"
        result = json.loads((run_dir / "result.json").read_text())
        assert result["status"] == "STOPPED"
        assert not (repo / ".solomon" / "worktrees").exists() or not any((repo / ".solomon" / "worktrees").iterdir())
        status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
        assert status.strip() == ""  # the original project is untouched
    finally:
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
