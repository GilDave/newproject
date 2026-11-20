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


PYTHONS = ("python", "python3")


def resolve_python(cmd: list[str] | None, project: Path) -> list[str] | None:
    """`python3 -m pytest` runs whatever python3 comes first on the PATH, which usually has no pytest.
    A project that has its own virtualenv is tested with that interpreter (the worktree has no .venv: it is not in git)."""
    if not cmd or os.sep in cmd[0] or os.path.basename(cmd[0]) not in PYTHONS:
        return cmd
    for rel in (".venv/bin/python", "venv/bin/python", ".venv/bin/python3", "venv/bin/python3"):
        cand = Path(project) / rel
        if cand.is_file():
            return [str(cand), *cmd[1:]]
    return cmd


def environment_hint(command: str, output: str) -> str:
    """Plain-language help when a test run fails for a reason that is the machine's, not the code's."""
    m = __import__("re").search(r"No module named '?(\w+)", output or "")
    if not m:
        return ""
    mod, exe = m.group(1), (command.split() or ["python3"])[0]
    return (f"\n→ 이 파이썬({exe})에 '{mod}' 모듈이 없습니다 (코드 문제가 아니라 테스트 환경 문제). 해결: 프로젝트 폴더에서 "
            f"`python3 -m venv .venv && .venv/bin/pip install {mod}` 를 실행하면 솔로몬이 그 .venv를 자동으로 씁니다.")


BASELINE_TTL_SEC = 12 * 3600


def baseline_key(commit: str, command: list[str]) -> str:
    return commit.strip() + "|" + " ".join(command)


def baseline_known_good(path: Path, key: str, now: float) -> bool:
    """True when this exact commit already passed this exact test command (a rerun would tell nothing new and can take minutes)."""
    import json

    try:
        seen = float(json.loads(Path(path).read_text(encoding="utf-8")).get(key) or 0)
    except (OSError, ValueError, AttributeError):
        return False
    return seen > 0 and 0 <= now - seen < BASELINE_TTL_SEC


def baseline_remember(path: Path, key: str, now: float) -> None:
    import json

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data = {k: v for k, v in data.items() if now - float(v or 0) < BASELINE_TTL_SEC}
    data[key] = now
    try:
        Path(path).write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass  # remembering is a convenience only


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
