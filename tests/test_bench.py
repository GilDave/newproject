import shutil
from pathlib import Path

import pytest

from solomon import bench
from solomon.orchestrator import Orchestrator
from solomon.providers import Provider, ProviderResult, QuotaExhausted
from solomon.settings import ConfigError, Settings

from helpers import order_json, review_json

TASKS = Path(__file__).resolve().parent.parent / "bench" / "tasks"


class RoleAwareFake(Provider):
    """Answers by what the prompt asks for, so it works as planner, builder or reviewer in any config."""

    def __init__(self, name, task_path, buggy=False, quota=False):
        self.name, self.task_path, self.buggy, self.quota = name, task_path, buggy, quota

    def run(self, prompt, cwd, write, timeout, no_output_timeout=None):
        if "You are the PLANNER" in prompt:
            text = order_json(allowed=("*.py", "tests/"), max_files_changed=5, max_diff_lines=400)
        elif "You are the REVIEWER" in prompt:
            text = review_json()
        else:
            if self.quota:
                raise QuotaExhausted("usage limit reached")
            if self.buggy:
                Path(cwd, "intervals.py").write_text("def merge_intervals(intervals):\n    return sorted(intervals)\n")
            else:
                for ref in (self.task_path / "reference").glob("*.py"):
                    shutil.copy(ref, Path(cwd) / ref.name)
            text = "done"
        return ProviderResult(text=text, exit_code=0, duration_sec=0.0, provider=self.name)


def factory_for(task, **kw):
    def make(settings):
        fake = RoleAwareFake("fake", task.path, **kw)
        return Orchestrator(settings, {"claude": fake, "codex": fake})

    return make


@pytest.fixture
def task():
    return bench.load_tasks(TASKS, ["t01"])[0]


def test_every_shipped_task_is_fair(tmp_path):
    tasks = bench.load_tasks(TASKS)
    assert len(tasks) >= 7
    assert bench.validate_tasks(tasks, tmp_path) == []


def test_validate_flags_a_task_whose_hidden_tests_cannot_pass(tmp_path, task):
    broken = tmp_path / "broken"
    shutil.copytree(task.path, broken)
    (broken / "reference" / "intervals.py").write_text("def merge_intervals(i):\n    return []\n")
    bad = bench.Task(task.id, task.title, task.goal, broken)
    problems = bench.validate_tasks([bad], tmp_path / "scratch")
    assert any("FAIL with the reference" in p for p in problems)


def test_correct_solution_is_scored_correct(tmp_path, task):
    recs = bench.run_bench([task], ["codex_build_claude_review"], tmp_path, Settings(), factory=factory_for(task), log=lambda m: None)
    r = recs[0]
    assert r["status"] == "PASSED" and r["hidden_pass"] is True
    row = bench.summarize(recs)[0]
    assert row["correct"] == 1 and row["escapes"] == 0 and row["escape_rate"] == 0


def test_defect_that_slips_through_review_is_counted_as_an_escape(tmp_path, task):
    recs = bench.run_bench([task], ["claude_solo"], tmp_path, Settings(), factory=factory_for(task, buggy=True), log=lambda m: None)
    r = recs[0]
    assert r["status"] == "PASSED" and r["hidden_pass"] is False  # the AIs said yes, the hidden tests say no
    row = bench.summarize(recs)[0]
    assert row["escapes"] == 1 and row["escape_rate"] == 1.0 and row["correct"] == 0
    assert "✗" in bench.format_report(recs)


def test_run_is_resumable_and_does_not_repeat_finished_work(tmp_path, task):
    args = ([task], ["codex_build_claude_review"], tmp_path, Settings())
    bench.run_bench(*args, factory=factory_for(task), log=lambda m: None)
    bench.run_bench(*args, factory=factory_for(task), log=lambda m: None)
    assert len(bench.load_results(tmp_path)) == 1


def test_quota_stops_the_experiment_and_is_excluded_from_the_numbers(tmp_path, task):
    logs = []
    recs = bench.run_bench(
        [task], ["codex_build_claude_review", "claude_solo"], tmp_path, Settings(),
        factory=factory_for(task, quota=True), log=logs.append,
    )
    assert len(recs) == 1 and recs[0]["status"] == "BLOCKED_QUOTA"  # stopped before the second config
    assert any("다시 실행" in m for m in logs)
    assert bench.summarize(recs) == []


def test_configs_set_roles_and_independence():
    solo = bench.settings_for(Settings(), "codex_solo")
    assert set(solo.roles.values()) == {"codex"} and solo.allow_same_provider is True
    cross = bench.settings_for(Settings(), "claude_build_codex_review")
    assert cross.roles["builder"] == "claude" and cross.roles["reviewer"] == "codex" and not cross.allow_same_provider
    with pytest.raises(ValueError):
        bench.settings_for(Settings(), "nope")


def test_goal_text_never_leaks_the_hidden_tests():
    for t in bench.load_tasks(TASKS):
        hidden = (t.path / "hidden" / bench.HIDDEN_NAME).read_text()
        assert "test_hidden" not in t.goal and "hidden" not in t.goal.lower()
        assert hidden.strip() not in t.goal
