"""The visual gate: run the project's capture command in the candidate's worktree and collect the screenshots it writes.

Exit code contract of the capture command (so a broken environment is never mistaken for a broken product):
  0 = pictures written to {out}; 3 = the product itself is broken (page did not render, script errors); anything else = the
  capture could not run (missing tool, timeout): reported as 'skipped', the result then needs a person's eyes.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .env import sanitized_env

BROKEN = 3


@dataclass
class VisualResult:
    ok: bool = False  # the product rendered
    broken: str = ""  # why the product is broken (exit code 3)
    infra: str = ""  # why the capture could not run (not the product's fault)
    shots: list[Path] = field(default_factory=list)


def run_capture(cfg: dict, wt: Path, out_dir: Path) -> VisualResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [str(c).replace("{out}", str(out_dir)).replace("{python}", sys.executable) for c in cfg.get("command") or []]
    if not cmd:
        return VisualResult(infra="캡처 명령이 비어 있습니다")
    try:
        res = subprocess.run(cmd, cwd=str(wt), capture_output=True, text=True, timeout=float(cfg.get("timeout") or 180), env=sanitized_env())
    except subprocess.TimeoutExpired:
        return VisualResult(infra=f"캡처가 {int(cfg.get('timeout') or 180)}초 안에 끝나지 않았습니다")
    except OSError as exc:
        return VisualResult(infra=f"캡처 명령을 실행하지 못했습니다: {exc}")
    tail = ((res.stdout or "") + (res.stderr or "")).strip()[-400:]
    if res.returncode == BROKEN:
        return VisualResult(broken=tail or "화면이 정상적으로 그려지지 않았습니다")
    if res.returncode != 0:
        return VisualResult(infra=f"캡처 명령이 종료 코드 {res.returncode}로 끝났습니다: {tail[-200:]}")
    shots = sorted(out_dir.glob("*.png"))[: int(cfg.get("max_shots") or 6)]
    if not shots:
        return VisualResult(infra="캡처 명령이 성공했지만 이미지가 없습니다")
    return VisualResult(ok=True, shots=shots)
