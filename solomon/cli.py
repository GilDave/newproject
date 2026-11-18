"""Command line: `solomon run "<goal>" --project PATH` and `solomon doctor`."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from . import __version__, gitops
from .env import present_api_keys
from .orchestrator import Orchestrator
from .providers import PROVIDER_CLASSES, build_providers
from .runner import run_process
from .status import load_runs, render
from . import bench as bench_mod
from .settings import ConfigError, load_settings

EXIT = {"PASSED": 0, "FAILED": 1, "BLOCKED_QUOTA": 3, "ERROR": 2}


def _doctor(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    providers = build_providers(settings)
    ok = True
    print(f"solomon {__version__}")
    print(f"roles: {settings.roles}")
    keys = present_api_keys()
    if keys:
        print(f"note: {', '.join(keys)} are set in this shell. Solomon removes them before every CLI call.")
    for name in PROVIDER_CLASSES:
        exe = providers[name].locate()  # type: ignore[attr-defined]
        if not exe:
            print(f"[missing] {name}: CLI not found. Install it and sign in with your subscription.")
            ok = False
            continue
        try:
            res = run_process([exe, "--version"], timeout=15)
            version = (res.stdout or res.stderr).strip().splitlines()[:1]
            print(f"[ok] {name}: {exe} {version[0] if version else ''}")
        except OSError as exc:
            print(f"[error] {name}: {exc}")
            ok = False
    print("Login state is not checked here. Run each CLI once by hand to confirm it is signed in.")
    return 0 if ok else 2


def _run(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    if args.on_quota:
        settings.on_quota = args.on_quota
        settings.validate()
    orch = Orchestrator(settings)
    project = Path(args.project or settings.project_path or ".").expanduser()
    result = orch.run(args.goal, project, keep_worktree=args.keep_worktree)
    print(f"run:      {result.run_id}")
    print(f"status:   {result.status}")
    print(f"attempts: {result.attempts}")
    print(f"summary:  {result.summary}")
    for w in result.warnings:
        print(f"warning:  {w}")
    print(f"log:      {result.run_dir}/events.jsonl")
    if result.status == "PASSED" and result.patch_path:
        print(f"patch:    {result.patch_path}")
        if args.apply:
            gitops.apply_patch(project.resolve(), Path(result.patch_path))
            print("applied the patch to the project working tree (not committed).")
        else:
            print(f"apply it with: git -C {project} apply {result.patch_path}")
    return EXIT.get(result.status, 2)


def _status(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    project = Path(args.project or settings.project_path or ".").expanduser().resolve()
    runs = load_runs(project, settings.state_dir)
    if args.run:
        runs = [r for r in runs if r.run_id.startswith(args.run)]
        if not runs:
            print(f"error: no run starting with '{args.run}'", file=sys.stderr)
            return 2
    print(render(runs, project, limit=args.limit))
    return 0


def _bench(args: argparse.Namespace) -> int:
    tasks_dir = Path(args.tasks)
    if not tasks_dir.is_dir():
        print(f"error: task folder not found: {tasks_dir} (run from the repository root or pass --tasks)", file=sys.stderr)
        return 2
    out = Path(args.out)
    if args.action == "report":
        print(bench_mod.format_report(bench_mod.load_results(out)))
        return 0
    only = [t for t in (args.only or "").split(",") if t]
    tasks = bench_mod.load_tasks(tasks_dir, only or None)
    if args.action == "validate":
        with tempfile.TemporaryDirectory() as scratch:
            problems = bench_mod.validate_tasks(tasks, Path(scratch))
        for p in problems:
            print(f"[problem] {p}")
        print(f"{len(tasks)} task(s) checked, {len(problems)} problem(s).")
        return 1 if problems else 0
    settings = load_settings(args.config)
    names = [c for c in (args.configs or ",".join(bench_mod.CONFIGS)).split(",") if c]
    for name in names:  # fail early on a typo, before any quota is spent
        bench_mod.settings_for(settings, name)
    problems = bench_mod.validate_tasks(tasks, Path(tempfile.mkdtemp()))
    if problems:
        print("\n".join(f"[problem] {p}" for p in problems), file=sys.stderr)
        return 2
    records = bench_mod.run_bench(tasks, names, out, settings, repeat=args.repeat, keep=args.keep)
    print()
    print(bench_mod.format_report(records))
    return 3 if any(r["status"] == "BLOCKED_QUOTA" for r in records) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="solomon", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the cross-verified pipeline for one goal")
    run.add_argument("goal", help="one-line goal")
    run.add_argument("--project", help="local path of the git project (default: `project` in solomon.toml, else the current folder)")
    run.add_argument("--config", help="path to solomon.toml")
    run.add_argument("--apply", action="store_true", help="apply the passed patch to the working tree")
    run.add_argument("--keep-worktree", action="store_true", help="keep the isolated worktree for inspection")
    run.add_argument("--on-quota", choices=["stop", "wait"], help="override loop.on_quota")
    run.set_defaults(func=_run)

    doc = sub.add_parser("doctor", help="check that the CLIs are installed")
    doc.add_argument("--config", help="path to solomon.toml")
    doc.set_defaults(func=_doctor)

    st = sub.add_parser("status", help="show progress of recent runs (read-only)")
    st.add_argument("--project", help="local path of the project (default: `project` in solomon.toml, else the current folder)")
    st.add_argument("--config", help="path to solomon.toml")
    st.add_argument("--run", help="show the run whose id starts with this text")
    st.add_argument("--limit", type=int, default=5, help="how many recent runs to list")
    st.set_defaults(func=_status)

    bn = sub.add_parser("bench", help="G2 experiment: compare single-model and cross-review configurations")
    bn.add_argument("action", choices=["validate", "run", "report"])
    bn.add_argument("--tasks", default="bench/tasks", help="folder with the experiment tasks")
    bn.add_argument("--only", help="comma-separated task ids or prefixes (default: all)")
    bn.add_argument("--configs", help=f"comma-separated, default all: {', '.join(bench_mod.CONFIGS)}")
    bn.add_argument("--repeat", type=int, default=1, help="repetitions of every task and config")
    bn.add_argument("--out", default="bench_results", help="folder for results.jsonl and run logs")
    bn.add_argument("--keep", action="store_true", help="keep each working repository for inspection")
    bn.add_argument("--config", help="path to solomon.toml")
    bn.set_defaults(func=_bench)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, gitops.GitError, ValueError) as exc:  # user mistakes: show a message, not a traceback
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        from .runner import kill_active

        kill_active()
        print("stopped.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
