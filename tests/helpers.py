"""Test doubles: a scripted provider and a throwaway git project."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from solomon.providers import Provider, ProviderResult
from solomon.settings import Settings


class ScriptedProvider(Provider):
    """Each call pops the next step. A step is a string, an exception, or fn(prompt, cwd) -> str."""

    def __init__(self, name: str, steps: list):
        self.name = name
        self.steps = list(steps)
        self.calls: list[dict] = []

    def run(self, prompt, cwd, write, timeout, no_output_timeout=None, images=None):
        self.calls.append({"prompt": prompt, "cwd": cwd, "write": write, "images": images})
        if not self.steps:
            raise AssertionError(f"{self.name}: no scripted step left for call #{len(self.calls)}")
        step = self.steps.pop(0)
        out = step(prompt, cwd) if callable(step) else step
        if isinstance(out, Exception):
            raise out
        return ProviderResult(text=out, exit_code=0, duration_sec=0.0, provider=self.name)


def order_json(allowed=("src/",), **overrides) -> str:
    data = {
        "allowed_paths": list(allowed),
        "protected_paths": [],
        "acceptance_criteria": ["src/new.py exists"],
        "allowed_deletions": [],
        "max_files_changed": 5,
        "max_diff_lines": 200,
        "context": "test",
    }
    data.update(overrides)
    return json.dumps(data)


def review_json(verdict="PASS", issues=None) -> str:
    return json.dumps({"verdict": verdict, "issues": issues or [], "summary": "ok"})


def write_file(rel: str, content: str = "x = 1\n"):
    def step(prompt, cwd):
        target = Path(cwd) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return "done"

    return step


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (repo / "README.md").write_text("# proj\n", encoding="utf-8")

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "tester")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    return repo


def make_settings(**overrides) -> Settings:
    s = Settings()
    # The 'verification' passes only if the builder created src/new.py in the worktree.
    s.test_command = ["python3", "-c", "import pathlib,sys; sys.exit(0 if pathlib.Path('src/new.py').exists() else 1)"]
    s.max_attempts = 3
    s.baseline_check = False
    s.auto_failover = False  # the older tests expect a limit to stop the run; failover tests turn it on
    for key, value in overrides.items():
        setattr(s, key, value)
    s.validate()
    return s
