"""G2 experiment harness: does cross review beat a single model?

Each task has a goal the AIs see and HIDDEN tests they never see. A run counts as a
defect escape when the pipeline says PASSED but the hidden tests fail. The hidden tests,
not the AIs, are the ground truth.
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import gitops
from .env import sanitized_env
from .orchestrator import Orchestrator
from .runner import run_process
from .settings import Settings
from .status import load_runs

# The four configurations compared by the G2 gate. In "solo" configs one model plans, builds and
# reviews (REDUCED_INDEPENDENCE); in the others the reviewer is a different provider than the builder.
CONFIGS: dict[str, dict] = {
    "codex_solo": {"roles": {"planner": "codex", "builder": "codex", "reviewer": "codex"}, "same": True},
    "claude_solo": {"roles": {"planner": "claude", "builder": "claude", "reviewer": "claude"}, "same": True},
    "codex_build_claude_review": {"roles": {"planner": "claude", "builder": "codex", "reviewer": "claude"}, "same": False},
    "claude_build_codex_review": {"roles": {"planner": "codex", "builder": "claude", "reviewer": "codex"}, "same": False},
}

# Extra configurations with Gemini; run them by name with --configs (they are not part of the default G2 set).
CONFIGS.update({
    "gemini_solo": {"roles": {"planner": "gemini", "builder": "gemini", "reviewer": "gemini"}, "same": True},
    "codex_build_gemini_review": {"roles": {"planner": "gemini", "builder": "codex", "reviewer": "gemini"}, "same": False},
    "claude_build_gemini_review": {"roles": {"planner": "gemini", "builder": "claude", "reviewer": "gemini"}, "same": False},
    "gemini_build_claude_review": {"roles": {"planner": "claude", "builder": "gemini", "reviewer": "claude"}, "same": False},
})
DEFAULT_CONFIGS = ["codex_solo", "claude_solo", "codex_build_claude_review", "claude_build_codex_review"]

HIDDEN_NAME = "test_hidden.py"


@dataclass
class Task:
    id: str
    title: str
    goal: str
    path: Path


def load_tasks(root: Path, only: Optional[list[str]] = None) -> list[Task]:
    tasks = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        meta = d / "task.toml"
        if not meta.is_file():
            continue
        if only and not any(d.name == o or d.name.startswith(o) for o in only):
            continue
        data = tomllib.loads(meta.read_text(encoding="utf-8"))
        tasks.append(Task(id=d.name, title=str(data.get("title", d.name)), goal=str(data["goal"]), path=d))
    if only and not tasks:
        raise ValueError(f"no task matches {only} in {root}")
    return tasks


def prepare_repo(task: Task, dest: Path) -> None:
    """A fresh git repository containing the task's starting files, one commit."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(task.path / "repo", dest)
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "bench@example.com"],
                 ["config", "user.name", "bench"], ["add", "-A"], ["commit", "-q", "-m", "start"]):
        gitops.git(args, dest)


def _pytest(project: Path, target: str) -> tuple[bool, str]:
    env = sanitized_env()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    res = run_process(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", target],
        cwd=str(project), env=env, timeout=120,
    )
    tail = (res.stdout + res.stderr).strip()[-600:]
    return (res.exit_code == 0 and not res.timed_out), tail


def score_patch(task: Task, patch: Path, work: Path) -> tuple[bool, str]:
    """Apply the pipeline's patch to a pristine copy and run the hidden tests."""
    fresh = work.parent / (work.name + "-score")
    prepare_repo(task, fresh)
    try:
        gitops.apply_patch(fresh, patch)
        shutil.copy(task.path / "hidden" / HIDDEN_NAME, fresh / HIDDEN_NAME)
        return _pytest(fresh, HIDDEN_NAME)
    except gitops.GitError as exc:
        return False, f"patch did not apply: {exc}"
    finally:
        shutil.rmtree(fresh, ignore_errors=True)


def validate_tasks(tasks: list[Task], scratch: Path) -> list[str]:
    """Prove each task is fair: hidden tests fail without an implementation and pass with the reference."""
    problems = []
    for task in tasks:
        work = scratch / f"validate-{task.id}"
        prepare_repo(task, work)
        shutil.copy(task.path / "hidden" / HIDDEN_NAME, work / HIDDEN_NAME)
        ok, _ = _pytest(work, HIDDEN_NAME)
        if ok:
            problems.append(f"{task.id}: hidden tests pass WITHOUT any implementation")
        for ref in (task.path / "reference").glob("*.py"):
            shutil.copy(ref, work / ref.name)
        ok, tail = _pytest(work, HIDDEN_NAME)
        if not ok:
            problems.append(f"{task.id}: hidden tests FAIL with the reference solution: {tail[-200:]}")
        shutil.rmtree(work, ignore_errors=True)
    return problems


def settings_for(base: Settings, config: str) -> Settings:
    if config not in CONFIGS:
        raise ValueError(f"unknown config '{config}' (known: {', '.join(CONFIGS)})")
    s = copy.deepcopy(base)
    s.roles = dict(CONFIGS[config]["roles"])
    s.allow_same_provider = bool(CONFIGS[config]["same"])
    s.validate()
    return s


def load_results(out_dir: Path) -> list[dict]:
    path = out_dir / "results.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _run_one(task, cname, rep, out_dir, base, keep, factory) -> dict:
    key = f"{task.id}__{cname}__{rep}"
    work = out_dir / "work" / key
    prepare_repo(task, work)
    settings = settings_for(base, cname)
    orch = factory(settings) if factory else Orchestrator(settings)
    started = time.time()
    result = orch.run(task.goal, work)
    elapsed = round(time.time() - started, 1)

    hidden_pass, detail = None, ""
    if result.status == "PASSED" and result.patch_path:
        hidden_pass, detail = score_patch(task, Path(result.patch_path), work)

    tokens: dict[str, int] = {}
    calls = 0
    runs = load_runs(work, settings.state_dir)
    if runs:
        for name, st in runs[-1].providers.items():
            tokens[name] = st.tokens
            calls += st.calls
    run_copy = out_dir / "runs" / key
    if Path(result.run_dir).is_dir():
        shutil.copytree(result.run_dir, run_copy, dirs_exist_ok=True)
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    return {
        "task": task.id, "config": cname, "rep": rep, "status": result.status,
        "attempts": result.attempts, "hidden_pass": hidden_pass, "hidden_detail": detail[-300:],
        "elapsed_sec": elapsed, "tokens": tokens, "calls": calls, "summary": result.summary[:300],
    }


def run_bench(
    tasks: list[Task],
    config_names: list[str],
    out_dir: Path,
    base: Settings,
    repeat: int = 1,
    keep: bool = False,
    factory: Optional[Callable[[Settings], Orchestrator]] = None,
    log: Callable[[str], None] = print,
) -> list[dict]:
    """Run every (task, config, repetition) not yet in results.jsonl. Stops on quota exhaustion; re-run to resume."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results = load_results(out_dir)
    done = {(r["task"], r["config"], r["rep"]) for r in results}
    total = len(tasks) * len(config_names) * repeat
    for rep in range(1, repeat + 1):
        for task in tasks:  # tasks outermost so a quota stop leaves every config with the same tasks done
            for cname in config_names:
                if (task.id, cname, rep) in done:
                    continue
                log(f"[{len(done) + 1}/{total}] {task.id} · {cname} · #{rep} ...")
                record = _run_one(task, cname, rep, out_dir, base, keep, factory)
                with (out_dir / "results.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                results.append(record)
                done.add((task.id, cname, rep))
                log(f"    -> {record['status']} · hidden={record['hidden_pass']} · attempts={record['attempts']} · {record['elapsed_sec']}s")
                if record["status"] == "BLOCKED_QUOTA":
                    log("구독 한도에 도달해 중단합니다. 한도가 풀린 뒤 같은 명령을 다시 실행하면 이어서 진행합니다.")
                    return results
    return results


def summarize(records: list[dict]) -> list[dict]:
    rows = []
    for cname in dict.fromkeys(r["config"] for r in records):
        rs = [r for r in records if r["config"] == cname and r["status"] != "BLOCKED_QUOTA"]
        if not rs:
            continue
        passed = [r for r in rs if r["status"] == "PASSED"]
        good = [r for r in passed if r["hidden_pass"]]
        escapes = len(passed) - len(good)
        n = len(rs)
        provs: dict[str, int] = {}
        for r in rs:
            for k, v in (r.get("tokens") or {}).items():
                provs[k] = provs.get(k, 0) + int(v)
        rows.append({
            "config": cname, "runs": n, "passed": len(passed), "correct": len(good), "escapes": escapes,
            "escape_rate": (escapes / len(passed)) if passed else None,
            "success_rate": len(good) / n,
            "avg_attempts": sum(r["attempts"] for r in rs) / n,
            "avg_sec": sum(r["elapsed_sec"] for r in rs) / n,
            "tokens_per_run": {k: v // n for k, v in provs.items()},
        })
    return rows


def format_report(records: list[dict]) -> str:
    rows = summarize(records)
    if not rows:
        return "아직 결과가 없습니다."
    lines = [
        "G2 검증 실험 결과",
        "정답 기준 = 숨은 테스트. '결함 통과' = 파이프라인은 통과시켰지만 숨은 테스트가 실패한 경우",
        "",
        f"{'구성':<28}{'실행':>4}{'통과':>5}{'정답':>5}{'결함통과':>8}{'누락률':>8}{'평균시도':>8}{'평균초':>7}  토큰/실행",
    ]
    for r in rows:
        rate = "-" if r["escape_rate"] is None else f"{r['escape_rate']:.0%}"
        tok = " ".join(f"{k}={v:,}" for k, v in sorted(r["tokens_per_run"].items()))
        lines.append(
            f"{r['config']:<28}{r['runs']:>4}{r['passed']:>5}{r['correct']:>5}{r['escapes']:>8}{rate:>8}"
            f"{r['avg_attempts']:>8.1f}{r['avg_sec']:>7.0f}  {tok}"
        )
    blocked = [r for r in records if r["status"] == "BLOCKED_QUOTA"]
    if blocked:
        lines.append(f"\n한도 소진으로 중단된 실행 {len(blocked)}건은 집계에서 제외했습니다.")
    lines.append("\n과제별 결과 (정답=○, 결함통과=✗, 파이프라인 실패=-)")
    tasks = list(dict.fromkeys(r["task"] for r in records))
    configs = [r["config"] for r in rows]
    lines.append(f"{'과제':<20}" + "".join(f"{c[:14]:>16}" for c in configs))
    for t in tasks:
        cells = []
        for c in configs:
            rs = [r for r in records if r["task"] == t and r["config"] == c and r["status"] != "BLOCKED_QUOTA"]
            if not rs:
                cells.append("")
                continue
            cells.append("".join("-" if r["status"] != "PASSED" else ("○" if r["hidden_pass"] else "✗") for r in rs))
        lines.append(f"{t:<20}" + "".join(f"{x:>16}" for x in cells))
    return "\n".join(lines)
