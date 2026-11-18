"""Deterministic verification: run the project's test command in the isolated worktree."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .env import sanitized_env
from .runner import run_process
from .settings import Settings

_TAIL = 4000


@dataclass
class VerifyResult:
    passed: bool
    skipped: bool
    command: str
    exit_code: int | None
    output_tail: str


def verify(settings: Settings, wt: Path) -> VerifyResult:
    cmd = settings.test_command
    if not cmd:
        return VerifyResult(True, True, "", None, "no test command configured: result is UNVERIFIED")
    base = os.path.basename(cmd[0])
    if base not in settings.approved_commands:
        return VerifyResult(False, False, " ".join(cmd), None, f"command '{base}' is not in approved_commands")
    try:
        res = run_process(
            cmd,
            cwd=str(wt),
            env=sanitized_env(),
            timeout=settings.timeouts["verify"],
            no_output_timeout=None,
        )
    except OSError as exc:
        return VerifyResult(False, False, " ".join(cmd), None, f"could not start test command: {exc}")
    output = (res.stdout + "\n" + res.stderr).strip()
    if res.timed_out:
        return VerifyResult(False, False, " ".join(cmd), res.exit_code, f"{res.timeout_reason}\n{output[-_TAIL:]}")
    return VerifyResult(res.exit_code == 0, False, " ".join(cmd), res.exit_code, output[-_TAIL:])
