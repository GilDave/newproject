"""Command line: `solomon run "<goal>" --project PATH` and `solomon doctor`."""

from __future__ import annotations

import argparse
import os
import shlex
import signal
import sys
import tempfile
from pathlib import Path

from . import __version__, analysis as analysis_mod, gitops
from .env import present_api_keys
from .orchestrator import Orchestrator
from .providers import PROVIDER_CLASSES, build_providers
from .runner import run_process
from .status import load_runs, render
from . import bench as bench_mod
from .settings import ALL_ROLES, ConfigError, load_settings

EXIT = {"PASSED": 0, "FAILED": 1, "BLOCKED_QUOTA": 3, "ERROR": 2, "STOPPED": 130}


def _doctor(args: argparse.Namespace) -> int:
    """Preflight: everything the guide's first step needs, each line saying what to do when it is not ok."""
    import platform
    import shutil

    settings = load_settings(args.config)
    providers = build_providers(settings)
    errors = 0
    print(f"solomon {__version__}  ·  python {platform.python_version()}")
    if sys.version_info < (3, 11):
        print("[error] Python 3.11 이상이 필요합니다. 맥: brew install python@3.12 후 새 가상환경을 만드세요.")
        errors += 1
    else:
        print("[ok] python 3.11+")
    git = shutil.which("git")
    if not git:
        print("[error] git이 없습니다. 맥: xcode-select --install")
        errors += 1
    else:
        mail = gitops.git(["config", "--get", "user.email"], ".", check=False).stdout.strip()
        if mail:
            print("[ok] git (사용자 정보 설정됨)")
        else:
            print('[warn] git 사용자 정보가 없습니다. 대시보드에서 커밋하려면: git config --global user.name "이름" && git config --global user.email "메일"')
    print(f"roles: {settings.roles}")
    keys = present_api_keys()
    if keys:
        print(f"note: {', '.join(keys)} 가 이 셸에 설정돼 있습니다. Solomon은 모든 CLI 호출 전에 이 값을 제거하므로 API 요금은 발생하지 않습니다.")
    needed = set(settings.roles.values())
    for name in PROVIDER_CLASSES:
        exe = providers[name].locate()  # type: ignore[attr-defined]
        required = name in needed
        if not exe:
            if required:
                print(f"[missing] {name}: CLI를 찾을 수 없습니다 (현재 역할 배정에 필요). 설치하고 구독 계정으로 로그인하세요.")
                errors += 1
            else:
                print(f"[optional] {name}: 설치 안 됨 (선택 사항. 없어도 동작하지만 3번째 AI 검수·분석은 쓸 수 없습니다).")
            continue
        try:
            res = run_process([exe, "--version"], timeout=15)
            version = (res.stdout or res.stderr).strip().splitlines()[:1]
            print(f"[ok] {name}: {exe} {version[0] if version else ''}")
        except OSError as exc:
            print(f"[error] {name}: {exc}")
            errors += 1
    root = Path(settings.projects_root).expanduser()
    probe = root if root.exists() else root.parent
    print(("[ok]" if probe.exists() and os.access(probe, os.W_OK) else "[warn]") + f" 새 프로젝트 폴더 위치: {root}")
    home = Path(os.environ.get("SOLOMON_HOME", str(Path.home() / ".solomon")))
    print(f"[ok] 상태 폴더: {home}" if (home.exists() or os.access(home.parent, os.W_OK)) else f"[warn] 상태 폴더를 만들 수 없습니다: {home}")
    print("로그인 상태는 여기서 확인하지 않습니다. 각 CLI를 직접 한 번 실행해 로그인돼 있는지 확인하세요 (가이드 1-3).")
    print("결과: " + ("문제 없음" if not errors else f"고쳐야 할 항목 {errors}개"))
    return 0 if not errors else 2


def _init(args: argparse.Namespace) -> int:
    """Write an editable solomon.toml next to where Solomon is run."""
    import importlib.resources as res

    dest = Path(args.path or "solomon.toml")
    if dest.exists() and not args.force:
        print(f"error: {dest} 가 이미 있습니다 (덮어쓰려면 --force).", file=sys.stderr)
        return 2
    dest.write_text(res.files("solomon").joinpath("solomon.example.toml").read_text(encoding="utf-8"), encoding="utf-8")
    print(f"설정 파일을 만들었습니다: {dest.resolve()}  (필요한 줄의 # 를 지우고 고치세요)")
    return 0


def _selftest(args: argparse.Namespace) -> int:
    from .selftest import run_selftest

    return run_selftest()


def _run(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    if args.on_quota:
        settings.on_quota = args.on_quota
        settings.validate()
    if args.persist:
        settings.max_attempts = 0  # keep going until it passes or a budget / the subscription runs out
    if args.max_attempts is not None:
        settings.max_attempts = args.max_attempts
    if args.max_minutes is not None:
        settings.max_run_minutes = args.max_minutes
    if args.max_tokens is not None:
        settings.max_tokens = args.max_tokens
    if args.no_double_check:
        settings.double_check_tests = False
    if args.no_baseline:
        settings.baseline_check = False
    settings.validate()
    for role in ALL_ROLES:
        chosen = getattr(args, role, None)
        if chosen:
            settings.roles[role] = chosen  # validated by the Orchestrator (known AI, builder can write, independence)
    if args.no_tests:
        settings.test_command = None
    elif args.test_command:
        settings.test_command = shlex.split(args.test_command)
    orch = Orchestrator(settings)
    project = Path(args.project or settings.project_path or ".").expanduser()

    def _stop(signum, frame):  # the dashboard's stop button sends SIGTERM
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
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


def _analyze(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    project = Path(args.project or settings.project_path or ".").expanduser()
    ais = [a for a in (args.ais or "").split(",") if a] or None

    def _stop(signum, frame):  # the dashboard's stop button sends SIGTERM
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    result = analysis_mod.run_analysis(settings, project, args.focus or "", ais)
    print(f"analysis: {result.analysis_id}")
    print(f"status:   {result.status}")
    print(f"summary:  {result.summary}")
    cov = result.coverage
    if cov:
        print(f"files:    {cov.get('included')} of {cov.get('total')} analysed ({cov.get('source')}), {cov.get('redactions')} secret-like values hidden")
    for name, info in result.ais.items():
        print(f"  {name:<12} {'ok' if info.get('ok') else 'FAILED: ' + str(info.get('error', ''))}")
    for c in result.clusters[:10]:
        where = f"{c['file']}:{c['line_start']}" if c["file"] else "?"
        mark = "verified" if c["verified"] else "UNVERIFIED"
        print(f"  [{c['severity']:<6}] {c['agreement']}/{len(result.ais)} AIs  {where}  {c['members'][0]['title']}  ({mark})")
    if result.report_path:
        print(f"report:   {result.report_path}")
    return {"DONE": 0, "FAILED": 1, "STOPPED": 130}.get(result.status, 2)


def _autofix(args: argparse.Namespace) -> int:
    from . import autofix as autofix_mod

    settings = load_settings(args.config)
    if args.no_tests:
        settings.test_command = None
    elif args.test_command:
        settings.test_command = shlex.split(args.test_command)
    for role in ALL_ROLES:
        chosen = getattr(args, role, None)
        if chosen:
            settings.roles[role] = chosen
    settings.validate()
    project = Path(args.project or settings.project_path or ".").expanduser()

    def _stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    try:
        res = autofix_mod.run_autofix(
            settings, project, args.analysis, min_severity=args.min_severity, min_agreement=args.min_agreement,
            verified_only=not args.include_unverified, max_fixes=args.max_fixes, budget_minutes=args.budget_minutes,
            allow_untested=args.allow_untested)
    except (autofix_mod.AutofixError, ConfigError, gitops.GitError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"batch:   {res.batch_id}\nstatus:  {res.status}\nsummary: {res.summary}")
    for i, it in enumerate(res.items, 1):
        print(f"  {i}. {it.status:<13} [{it.severity}] {it.title}")
    if res.branch and res.patch_path:
        print(f"branch:  {res.branch}")
    print(f"report:  {res.report_path}")
    return {"DONE": 0, "NOTHING": 0, "PARTIAL": 1, "FAILED": 1, "STOPPED": 130}.get(res.status, 2)


def _runtests(args: argparse.Namespace) -> int:
    """Run the project's test command in the working folder (used by the dashboard's test button)."""
    from .verifier import verify

    settings = load_settings(args.config)
    if args.test_command:
        settings.test_command = shlex.split(args.test_command)
    project = Path(args.project or settings.project_path or ".").expanduser()
    res = verify(settings, project)
    print(f"명령: {res.command or '(없음)'}")
    print(res.output_tail)
    print("결과: " + ("통과" if res.passed and not res.skipped else "테스트 없음" if res.skipped else f"실패 (종료 코드 {res.exit_code})"))
    return 0 if res.passed else 1


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
    names = [c for c in (args.configs or ",".join(bench_mod.DEFAULT_CONFIGS)).split(",") if c]
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


def _dashboard(args: argparse.Namespace) -> int:
    from . import dashboard

    settings = load_settings(args.config)
    return dashboard.serve(settings, args.project or [], args.config, args.port, not args.no_open)


def build_parser() -> argparse.ArgumentParser:
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
    for role in ALL_ROLES:
        run.add_argument(f"--{role}", help=f"which AI takes the {role} role for this run (overrides the config)")
    run.add_argument("--test-command", help="test command for this run, e.g. 'npm test' (overrides the config)")
    run.add_argument("--persist", action="store_true", help="no attempt limit: keep trying until it passes or a budget runs out")
    run.add_argument("--max-attempts", type=int, help="attempt limit (0 = none)")
    run.add_argument("--max-minutes", type=float, help="wall-clock budget of the run in minutes")
    run.add_argument("--max-tokens", type=int, help="token budget of the run")
    run.add_argument("--no-baseline", action="store_true", help="skip the test run on the untouched code (for a brand-new project)")
    run.add_argument("--no-double-check", action="store_true", help="do not re-run the tests before declaring a pass")
    run.add_argument("--no-tests", action="store_true", help="run without automatic tests (the result is UNVERIFIED)")
    run.set_defaults(func=_run)

    ini = sub.add_parser("init", help="write an editable solomon.toml (all options, commented)")
    ini.add_argument("--path", help="where to write it (default: ./solomon.toml)")
    ini.add_argument("--force", action="store_true")
    ini.set_defaults(func=_init)

    stt = sub.add_parser("selftest", help="check the installation end to end with fake AIs (no subscription use, no tokens)")
    stt.set_defaults(func=_selftest)

    doc = sub.add_parser("doctor", help="preflight: Python, git, the AI CLIs, folders")
    doc.add_argument("--config", help="path to solomon.toml")
    doc.set_defaults(func=_doctor)

    an = sub.add_parser("analyze", help="read-only analysis of a project by several AIs (never modifies it)")
    an.add_argument("--project", help="local path of the git project (default: `project` in solomon.toml, else the current folder)")
    an.add_argument("--focus", help="what to look at, e.g. 'places that can crash' (default: a general analysis)")
    an.add_argument("--ais", help="comma-separated AIs to use (default: every installed one)")
    an.add_argument("--config", help="path to solomon.toml")
    an.set_defaults(func=_analyze)

    af = sub.add_parser("autofix", help="fix the findings of an analysis one after another, unattended (own branch, original untouched)")
    af.add_argument("--project")
    af.add_argument("--config")
    af.add_argument("--analysis", help="analysis id (default: the latest)")
    af.add_argument("--min-severity", choices=["low", "medium", "high"], default="medium")
    af.add_argument("--min-agreement", type=int, default=1, help="how many AIs must have reported the finding")
    af.add_argument("--include-unverified", action="store_true", help="also fix findings whose quoted evidence was not found")
    af.add_argument("--max-fixes", type=int, default=5)
    af.add_argument("--budget-minutes", type=float, default=360.0)
    af.add_argument("--test-command")
    af.add_argument("--no-tests", action="store_true")
    af.add_argument("--allow-untested", action="store_true")
    for role in ALL_ROLES:
        af.add_argument(f"--{role}")
    af.set_defaults(func=_autofix)

    rt = sub.add_parser("runtests", help="run the project's test command in its working folder")
    rt.add_argument("--project")
    rt.add_argument("--config")
    rt.add_argument("--test-command")
    rt.set_defaults(func=_runtests)

    st = sub.add_parser("status", help="show progress of recent runs (read-only)")
    st.add_argument("--project", help="local path of the project (default: `project` in solomon.toml, else the current folder)")
    st.add_argument("--config", help="path to solomon.toml")
    st.add_argument("--run", help="show the run whose id starts with this text")
    st.add_argument("--limit", type=int, default=5, help="how many recent runs to list")
    st.set_defaults(func=_status)

    db = sub.add_parser("dashboard", help="open the local web dashboard (watch runs and give commands)")
    db.add_argument("--project", action="append", help="project folder to show (repeatable)")
    db.add_argument("--port", type=int, default=8765)
    db.add_argument("--no-open", action="store_true", help="do not open the browser")
    db.add_argument("--config", help="path to solomon.toml")
    db.set_defaults(func=_dashboard)

    bn = sub.add_parser("bench", help="G2 experiment: compare single-model and cross-review configurations")
    bn.add_argument("action", choices=["validate", "run", "report"])
    bn.add_argument("--tasks", default="bench/tasks", help="folder with the experiment tasks")
    bn.add_argument("--only", help="comma-separated task ids or prefixes (default: all)")
    bn.add_argument("--configs", help=f"comma-separated, default: {', '.join(bench_mod.DEFAULT_CONFIGS)}; also: {', '.join(c for c in bench_mod.CONFIGS if c not in bench_mod.DEFAULT_CONFIGS)}")
    bn.add_argument("--repeat", type=int, default=1, help="repetitions of every task and config")
    bn.add_argument("--out", default="bench_results", help="folder for results.jsonl and run logs")
    bn.add_argument("--keep", action="store_true", help="keep each working repository for inspection")
    bn.add_argument("--config", help="path to solomon.toml")
    bn.set_defaults(func=_bench)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
