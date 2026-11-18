"""Command line: `solomon run "<goal>" --project PATH` and `solomon doctor`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, gitops
from .env import present_api_keys
from .orchestrator import Orchestrator
from .providers import PROVIDER_CLASSES, build_providers
from .runner import run_process
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
    project = Path(args.project)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="solomon", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the cross-verified pipeline for one goal")
    run.add_argument("goal", help="one-line goal")
    run.add_argument("--project", default=".", help="path of the git project (default: current folder)")
    run.add_argument("--config", help="path to solomon.toml")
    run.add_argument("--apply", action="store_true", help="apply the passed patch to the working tree")
    run.add_argument("--keep-worktree", action="store_true", help="keep the isolated worktree for inspection")
    run.add_argument("--on-quota", choices=["stop", "wait"], help="override loop.on_quota")
    run.set_defaults(func=_run)

    doc = sub.add_parser("doctor", help="check that the CLIs are installed")
    doc.add_argument("--config", help="path to solomon.toml")
    doc.set_defaults(func=_doctor)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, gitops.GitError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        from .runner import kill_active

        kill_active()
        print("stopped.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
