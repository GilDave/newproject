import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from solomon import analysis, cli

from test_analysis import Fake, good, make_bot_repo
from test_stop import alive, wait_for

ROOT = Path(__file__).resolve().parent.parent


def test_analyze_command_prints_a_summary_and_writes_the_report(tmp_path, monkeypatch, capsys):
    repo = make_bot_repo(tmp_path)
    monkeypatch.setattr(analysis, "build_providers", lambda settings: {"claude": Fake("claude", [good()]), "codex": Fake("codex", [good(line=8)])})
    code = cli.main(["analyze", "--project", str(repo), "--focus", "에러 위주", "--ais", "claude,codex"])
    out = capsys.readouterr().out
    assert code == 0 and "status:   DONE" in out and "files:" in out and "secret-like values hidden" in out
    assert "2/2 AIs" in out and "src/bot.py" in out and "(verified)" in out
    report = Path(out.split("report:")[1].split()[0])
    assert report.is_file() and "프로젝트 분석 보고서" in report.read_text()


def test_analyze_command_rejects_bad_input_with_a_message(tmp_path, capsys):
    assert cli.main(["analyze", "--project", str(tmp_path)]) == 2  # not a git repository
    assert "not a git repository" in capsys.readouterr().err
    repo = make_bot_repo(tmp_path)
    assert cli.main(["analyze", "--project", str(repo), "--ais", "nope"]) == 2
    assert "unknown AI" in capsys.readouterr().err


def test_sigterm_stops_an_analysis_cleanly_and_leaves_no_ai_process(tmp_path):
    repo = make_bot_repo(tmp_path)
    pids = tmp_path / "pids"
    pids.mkdir()
    fake = tmp_path / "fakecli"
    fake.write_text(f"#!{sys.executable}\nimport os, sys, time\nopen({str(pids)!r} + '/' + str(os.getpid()), 'w').write('x')\n"
                    "sys.stdin.read()\ntime.sleep(300)\n")
    fake.chmod(0o755)
    cfg = tmp_path / "solomon.toml"
    cfg.write_text(f'[executables]\nclaude = "{fake}"\ncodex = "{fake}"\n')
    proc = subprocess.Popen(
        [sys.executable, "-m", "solomon", "analyze", "--project", str(repo), "--ais", "claude,codex", "--config", str(cfg)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True, cwd=str(ROOT))
    try:
        assert wait_for(lambda: len(list(pids.iterdir())) == 2), "both AIs should have been started in parallel"
        fake_pids = [int(p.name) for p in pids.iterdir()]
        assert all(alive(p) for p in fake_pids)

        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)  # exactly what the dashboard's stop button does
        out, _ = proc.communicate(timeout=30)

        assert proc.returncode == 130, out
        assert wait_for(lambda: not any(alive(p) for p in fake_pids)), "an AI process was left running"
        adir = next((repo / ".solomon" / "analyses").iterdir())
        kinds = [json.loads(l)["event"] for l in (adir / "events.jsonl").read_text().splitlines()]
        assert kinds[-1] == "analysis_end"
        assert json.loads((adir / "analysis.json").read_text())["status"] == "STOPPED"
        assert subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout.strip() == ""
    finally:
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
