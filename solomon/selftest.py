"""`solomon selftest`: the whole machine, end to end, with scripted fake AIs in a throw-away folder.

It uses no subscription and no tokens. It proves that this installation can: run the cross-verified pipeline,
reject out-of-scope edits, send review failures back, keep the original untouched, snapshot uncommitted edits,
apply / commit / undo / discard / restore results, and create / trash / restore project folders.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from . import gitops, manage, projects
from .orchestrator import Orchestrator
from .providers.base import Provider, ProviderResult
from .settings import Settings


class _Fake(Provider):
    def __init__(self, name: str, steps: list):
        self.name, self.steps = name, list(steps)

    def run(self, prompt, cwd, write, timeout, no_output_timeout=None):
        step = self.steps.pop(0)
        out = step(prompt, cwd) if callable(step) else step
        return ProviderResult(text=out, exit_code=0, duration_sec=0.0, provider=self.name)


def _order() -> str:
    return json.dumps({"allowed_paths": ["src/"], "protected_paths": [], "acceptance_criteria": ["src/new.py exists"],
                       "allowed_deletions": [], "max_files_changed": 5, "max_diff_lines": 200, "context": "selftest"})


def _review(verdict: str = "PASS", issues=None) -> str:
    return json.dumps({"verdict": verdict, "issues": issues or [], "summary": "selftest"})


def _write(rel: str, content: str = "x = 1\n"):
    def step(prompt, cwd):
        t = Path(cwd) / rel
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(content, encoding="utf-8")
        return "done"
    return step


def _repo(base: Path, name: str) -> Path:
    repo = base / name
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (repo / "README.md").write_text("# demo\n", encoding="utf-8")
    for args in (["init", "-q"], ["config", "user.email", "t@example.com"], ["config", "user.name", "selftest"], ["add", "-A"],
                 ["commit", "-q", "-m", "init"]):
        gitops.git(args, repo)
    return repo


def _settings() -> Settings:
    s = Settings()
    s.test_command = ["python3", "-c", "import pathlib,sys; sys.exit(0 if pathlib.Path('src/new.py').exists() else 1)"]
    s.baseline_check = False
    s.max_attempts = 3
    return s


def _orch(claude: list, codex: list, s: Settings | None = None) -> Orchestrator:
    return Orchestrator(s or _settings(), {"claude": _Fake("claude", claude), "codex": _Fake("codex", codex)})


def run_selftest(out: Callable[[str], None] = print) -> int:
    results: list[tuple[str, bool, str]] = []

    def check(title: str, fn: Callable[[], None]) -> None:
        t0 = time.time()
        try:
            fn()
            results.append((title, True, ""))
            out(f"  ✔ {title} ({time.time() - t0:.1f}s)")
        except Exception as exc:  # report every failing step, not just the first
            results.append((title, False, f"{type(exc).__name__}: {exc}"))
            out(f"  ✘ {title}\n      {type(exc).__name__}: {exc}")

    with tempfile.TemporaryDirectory(prefix="solomon-selftest-") as tmp:
        base = Path(tmp)
        out("Solomon 자체 점검 (가짜 AI 사용 · 구독/토큰을 쓰지 않습니다)")
        state: dict = {}

        def pipeline():
            repo = _repo(base, "p1")
            r = _orch([_order(), _review()], [_write("src/new.py")]).run("add module", repo)
            assert r.status == "PASSED" and r.attempts == 1, r.summary
            assert not (repo / "src" / "new.py").exists(), "원본이 바뀌었습니다"
            assert "src/new.py" in Path(r.patch_path).read_text()
            state["repo"], state["run"] = repo, r

        def scope_and_retry():
            repo = _repo(base, "p2")
            r = _orch([_order(), _review("FAIL", ["add a docstring"]), _review()],
                      [_write("README.md", "hacked\n"), _write("src/new.py"), _write("src/new.py", '"""doc"""\nx = 1\n')]).run("add module", repo)
            assert r.status == "PASSED" and r.attempts == 3, f"{r.status} {r.attempts}"
            assert "hacked" not in Path(r.patch_path).read_text(), "범위 밖 수정이 결과에 섞였습니다"

        def snapshot():
            repo = _repo(base, "p3")
            (repo / "src" / "app.py").write_text("print('uncommitted edit')\n")
            (repo / ".env").write_text("SECRET=1\n")
            seen = {}

            def spy(prompt, cwd):
                seen["edit"] = "uncommitted edit" in (Path(cwd) / "src" / "app.py").read_text()
                seen["env"] = (Path(cwd) / ".env").exists()
                return _write("src/new.py")(prompt, cwd)
            r = _orch([_order(), _review()], [spy]).run("add module", repo)
            assert r.status == "PASSED" and seen == {"edit": True, "env": False}, seen

        def apply_commit_undo():
            repo, run = state["repo"], state["run"]
            key = f"run:{run.run_id}"
            sd = ".solomon"
            manage.perform(repo, sd, key, "apply")
            assert (repo / "src" / "new.py").exists()
            manage.perform(repo, sd, key, "revert")
            assert not (repo / "src" / "new.py").exists()
            manage.perform(repo, sd, key, "apply")
            manage.perform(repo, sd, key, "commit", "selftest commit")
            assert "selftest commit" in gitops.git(["log", "-1", "--format=%s"], repo).stdout
            manage.perform(repo, sd, key, "revert_commit")
            assert not (repo / "src" / "new.py").exists()
            assert manage.list_inbox(repo, sd)["entries"][0]["state"] == "pending"

        def discard_restore():
            repo, run = state["repo"], state["run"]
            key = f"run:{run.run_id}"
            manage.perform(repo, ".solomon", key, "discard")
            manage.perform(repo, ".solomon", key, "restore")
            assert manage.list_inbox(repo, ".solomon")["entries"][0]["state"] == "pending"

        def five_w_one_h():
            d = manage.describe(state["repo"], ".solomon", f"run:{state['run'].run_id}")
            assert list(d["sections"]) == ["who", "what", "when", "where", "why", "how"]
            assert d["assessment"]["recommendation"] == "recommend", d["assessment"]

        def project_folders():
            root = base / "root"
            made = projects.create_project(root, "demo-app", "python", "demo")
            path = Path(made["path"])
            assert (path / "tests" / "test_smoke.py").is_file() and gitops.git(["rev-list", "--count", "HEAD"], path).stdout.strip() == "1"
            trash = base / "trash"
            projects.trash_project(path, root, trash, "demo-app")
            assert not path.exists()
            projects.restore_project(trash, projects.list_trash(trash)[0]["id"], root)
            assert path.is_dir()
            outside = _repo(base, "other-project")
            try:
                projects.trash_project(outside, root, trash, "other-project")
            except projects.ProjectError:
                assert outside.is_dir()
            else:
                raise AssertionError("Solomon이 만들지 않은 프로젝트가 삭제 허용됐습니다")

        for title, fn in (
            ("교차 검증 파이프라인 (계획 → 구현 → 범위 검사 → 테스트 → 검수)", pipeline),
            ("범위 밖 수정 차단 · 검수 불합격 시 재시도", scope_and_retry),
            ("커밋 안 한 수정 자동 포함 · 비밀 파일 제외", snapshot),
            ("적용 → 되돌리기 → 커밋 → 커밋 취소", apply_commit_undo),
            ("폐기 → 복원", discard_restore),
            ("5W1H 기록과 적용 판단", five_w_one_h),
            ("신규 프로젝트 폴더 · 휴지통 · 복원 · 기존 프로젝트 삭제 거부", project_folders),
        ):
            check(title, fn)
        subprocess.run(["git", "worktree", "prune"], cwd=base, capture_output=True)

    bad = [r for r in results if not r[1]]
    out(f"결과: {len(results) - len(bad)}/{len(results)} 통과" + ("" if not bad else " — 실패 항목이 있으면 이 출력을 그대로 보내주세요."))
    return 0 if not bad else 1
