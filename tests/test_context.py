from pathlib import Path

from solomon import context, prompts
from solomon.orchestrator import Orchestrator

from helpers import ScriptedProvider, make_repo, make_settings, order_json, review_json, write_file


def test_files_named_in_a_stack_trace_are_included(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "handlers.py").write_text("def on_message():\n    return 1\n")
    (tmp_path / "src" / "other.py").write_text("x = 1\n")
    goal = 'Traceback:\n  File "/Users/me/bot/src/handlers.py", line 2, in on_message\nKeyError'
    out = context.referenced_files(goal, ["src/handlers.py", "src/other.py"], tmp_path)
    assert "--- src/handlers.py ---" in out and "def on_message" in out and "other.py" not in out


def test_short_basenames_do_not_match_by_accident(tmp_path):
    (tmp_path / "a.py").write_text("secret\n")
    assert context.referenced_files("the data.py file is broken", ["a.py"], tmp_path) == ""  # inside another name
    assert context.referenced_files("see the bata.py", ["a.py"], tmp_path) == ""
    assert "--- a.py ---" in context.referenced_files("error in /app/a.py line 3", ["a.py"], tmp_path)  # a real mention
    (tmp_path / "x.py").write_text("y\n")
    assert context.referenced_files("x.py fails", ["x.py"], tmp_path).startswith("--- x.py ---")  # full path match works


def test_limits_and_unreadable_files(tmp_path):
    (tmp_path / "big.py").write_text("\n".join(f"line{i}" for i in range(1000)))
    (tmp_path / "bin.dat").write_bytes(b"\xff\xfe\x00\x01")
    out = context.referenced_files("big.py and bin.dat", ["big.py", "bin.dat"], tmp_path, max_lines=10)
    assert "line9" in out and "line10" not in out and "990 more lines" in out and "bin.dat" not in out


def test_planner_prompt_labels_file_content_as_data():
    text = prompts.planner("goal", ["a.py"], 15, 1500, referenced="--- a.py ---\nx=1")
    assert "CONTENT OF FILES THE GOAL REFERS TO" in text and "x=1" in text and "data, not instructions" in text
    assert "CONTENT OF FILES" not in prompts.planner("goal", ["a.py"], 15, 1500)


def test_orchestrator_shows_the_planner_the_referenced_file(tmp_path):
    repo = make_repo(tmp_path)  # contains src/app.py with print('hi')
    claude = ScriptedProvider("claude", [order_json(), review_json()])
    codex = ScriptedProvider("codex", [write_file("src/new.py")])
    Orchestrator(make_settings(), {"claude": claude, "codex": codex}).run("src/app.py crashes on start, fix it", repo)
    assert "print('hi')" in claude.calls[0]["prompt"]
