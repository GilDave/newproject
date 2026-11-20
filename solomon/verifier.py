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


def failure_digest(output: str, limit: int = 3500) -> str:
    """The part of a test run a builder needs to fix the failure: the failing test names plus the first tracebacks.

    A plain tail of the output often holds only the last traceback or the final summary line; this keeps the names of EVERY
    failing test and the first two failure blocks (their assertion text), within `limit` characters."""
    import re

    lines = output.splitlines()
    names = [l.strip() for l in lines if re.match(r"\s*(FAILED|ERROR)\s+\S+", l)]
    blocks: list[str] = []
    cur: list[str] | None = None
    for l in lines:
        if re.match(r"^_{3,}\s.+\s_{3,}$", l):  # "____ test_name ____"
            if cur:
                blocks.append("\n".join(cur[:45]))
            cur = [l]
        elif cur is not None and (re.match(r"^={3,}\s", l) and not l.startswith("=== FAILURES")):
            blocks.append("\n".join(cur[:45]))
            cur = None
        elif cur is not None:
            cur.append(l)
    if cur:
        blocks.append("\n".join(cur[:45]))
    summary = [l for l in lines[-6:] if l.strip()]
    parts = []
    if names:
        parts.append("Failing tests:\n" + "\n".join(dict.fromkeys(names[:30])))
    if blocks:
        parts.append("First failures in detail:\n" + "\n\n".join(blocks[:2]))
    parts.append("End of output:\n" + "\n".join(summary))
    text = "\n\n".join(parts)
    return text[:limit] if len(text) > limit else text
