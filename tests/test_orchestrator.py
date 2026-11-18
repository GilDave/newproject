import json
import subprocess
from pathlib import Path

import pytest

from solomon.orchestrator import Orchestrator
from solomon.providers import ProviderError, QuotaExhausted
from solomon.settings import ConfigError

from helpers import ScriptedProvider, make_repo, make_settings, order_json, review_json, write_file


def build(settings, claude_steps, codex_steps, sleep=None):
    claude = ScriptedProvider("claude", claude_steps)
    codex = ScriptedProvider("codex", codex_steps)
    kwargs = {"sleep": sleep} if sleep else {}
    return Orchestrator(settings, {"claude": claude, "codex": codex}, **kwargs), claude, codex


def test_happy_path_cross_review_and_isolation(tmp_path):
    repo = make_repo(tmp_path)
    orch, claude, codex = build(make_settings(), [order_json(), review_json()], [write_file("src/new.py")])
    result = orch.run("add new module", repo)

    assert result.status == "PASSED"
    assert result.attempts == 1
    assert "src/new.py" in Path(result.patch_path).read_text()
    # the original project is never modified, the worktree is cleaned up
    assert not (repo / "src" / "new.py").exists()
    assert not (Path(result.run_dir).parent.parent / "worktrees" / result.run_id).exists()
    # planner and reviewer are claude, builder is codex; reviewer is read-only, builder can write
    assert [c["write"] for c in codex.calls] == [True]
    assert [c["write"] for c in claude.calls] == [False, False]
    events = [json.loads(l)["event"] for l in (Path(result.run_dir) / "events.jsonl").read_text().splitlines()]
    assert events[0] == "run_start" and events[-1] == "run_end"


def test_scope_violation_is_discarded_and_retried(tmp_path):
    repo = make_repo(tmp_path)
    orch, _, codex = build(
        make_settings(),
        [order_json(), review_json()],
        [write_file("README.md", "hacked\n"), write_file("src/new.py")],
    )
    result = orch.run("add new module", repo)

    assert result.status == "PASSED"
    assert result.attempts == 2
    assert "outside allowed_paths" in codex.calls[1]["prompt"]
    assert "hacked" not in Path(result.patch_path).read_text()


def test_reviewer_fail_feeds_issues_back_to_builder(tmp_path):
    repo = make_repo(tmp_path)
    orch, _, codex = build(
        make_settings(),
        [order_json(), review_json("FAIL", ["missing edge case test"]), review_json()],
        [write_file("src/new.py"), write_file("src/new.py", "x = 2\n")],
    )
    result = orch.run("add new module", repo)

    assert result.status == "PASSED"
    assert result.attempts == 2
    assert "missing edge case test" in codex.calls[1]["prompt"]


def test_unparseable_review_fails_closed(tmp_path):
    repo = make_repo(tmp_path)
    settings = make_settings(max_attempts=2)
    orch, *_ = build(
        settings,
        [order_json(), "looks good to me", "still fine"],
        [write_file("src/new.py"), write_file("src/new.py")],
    )
    result = orch.run("add new module", repo)

    assert result.status == "FAILED"
    assert result.patch_path is None


def test_failing_tests_block_a_passing_review(tmp_path):
    repo = make_repo(tmp_path)
    settings = make_settings(max_attempts=1)
    orch, *_ = build(settings, [order_json(), review_json()], [write_file("src/other.py")])
    result = orch.run("add new module", repo)

    assert result.status == "FAILED"


def test_quota_stops_the_run_without_api_fallback(tmp_path):
    repo = make_repo(tmp_path)
    orch, *_ = build(make_settings(), [order_json()], [QuotaExhausted("usage limit reached")])
    result = orch.run("add new module", repo)

    assert result.status == "BLOCKED_QUOTA"
    assert "codex" in result.summary


def test_quota_wait_retries_the_same_call(tmp_path):
    repo = make_repo(tmp_path)
    sleeps = []
    settings = make_settings(on_quota="wait", quota_wait_sec=7.0)
    orch, *_ = build(
        settings,
        [order_json(), review_json()],
        [QuotaExhausted("usage limit reached"), write_file("src/new.py")],
        sleep=sleeps.append,
    )
    result = orch.run("add new module", repo)

    assert result.status == "PASSED"
    assert sleeps == [7.0]


def test_invalid_order_is_retried_once_then_errors(tmp_path):
    repo = make_repo(tmp_path)
    orch, claude, _ = build(make_settings(), ["not json", order_json(allowed=["/etc"])], [])
    result = orch.run("add new module", repo)

    assert result.status == "ERROR"
    assert "ORDER" in result.summary
    assert len(claude.calls) == 2


def test_planner_cannot_loosen_limits(tmp_path):
    repo = make_repo(tmp_path)
    orch, _, _ = build(
        make_settings(),
        [order_json(max_files_changed=9999, max_diff_lines=999999), review_json()],
        [write_file("src/new.py")],
    )
    orch.s.max_attempts = 1
    orch.run("add new module", repo)
    events = (next((repo / ".solomon" / "runs").iterdir()) / "events.jsonl").read_text().splitlines()
    order = next(json.loads(l)["order"] for l in events if json.loads(l)["event"] == "order")
    assert order["max_files_changed"] == 15 and order["max_diff_lines"] == 1500


def test_same_provider_for_builder_and_reviewer_is_rejected_by_default():
    settings = make_settings()
    settings.roles["reviewer"] = "codex"
    with pytest.raises(ConfigError):
        Orchestrator(settings, {"claude": ScriptedProvider("claude", []), "codex": ScriptedProvider("codex", [])})


def test_same_provider_allowed_is_flagged(tmp_path):
    repo = make_repo(tmp_path)
    settings = make_settings(allow_same_provider=True)
    settings.roles["reviewer"] = "codex"
    codex = ScriptedProvider("codex", [write_file("src/new.py"), review_json()])
    claude = ScriptedProvider("claude", [order_json()])
    result = Orchestrator(settings, {"claude": claude, "codex": codex}).run("add new module", repo)

    assert result.status == "PASSED"
    assert result.reduced_independence is True
    assert any("REDUCED_INDEPENDENCE" in w for w in result.warnings)


def test_builder_cli_failure_counts_as_an_attempt(tmp_path):
    repo = make_repo(tmp_path)
    orch, _, codex = build(
        make_settings(),
        [order_json(), review_json()],
        [ProviderError("codex exited with 1: boom"), write_file("src/new.py")],
    )
    result = orch.run("add new module", repo)

    assert result.status == "PASSED"
    assert result.attempts == 2
    assert "boom" in codex.calls[1]["prompt"]


def test_project_is_left_clean_and_state_dir_is_excluded(tmp_path):
    repo = make_repo(tmp_path)
    orch, *_ = build(make_settings(), [order_json(), review_json()], [write_file("src/new.py")])
    orch.run("add new module", repo)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert status.strip() == ""
