"""Per-project policy, stored in the project's own Solomon state folder (`.solomon/policy.json`).

It is read by the pipeline itself (not only by the dashboard), so it holds no matter how a run is started:
  protect: paths no AI may change in any run of this project (merged into every ORDER; the planner cannot remove them)
  shadow : the arbiter judges every attempt but is never obeyed (answers are only recorded and scored)
"""

from __future__ import annotations

import json
from pathlib import Path

from .schemas import _check_pattern


def path_of(project: Path, state_dir: str) -> Path:
    return Path(project) / state_dir / "policy.json"


def load(project: Path, state_dir: str) -> dict:
    try:
        data = json.loads(path_of(project, state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"protect": [], "shadow": False, "visual": None}
    prot = []
    for p in data.get("protect") or []:
        try:
            prot.append(_check_pattern(str(p)))
        except ValueError:
            continue  # a bad pattern must not disable the others
    return {"protect": list(dict.fromkeys(prot)), "shadow": bool(data.get("shadow")), "visual": _visual(data.get("visual"))}


def _visual(v: object) -> dict | None:
    """{command: [...], timeout, max_shots} or None. A malformed entry is ignored, never half-applied."""
    if not isinstance(v, dict) or not isinstance(v.get("command"), list) or not v["command"] or len(v["command"]) > 20:
        return None
    cmd = [str(c) for c in v["command"] if str(c).strip()]
    if not cmd:
        return None
    try:
        return {"command": cmd, "timeout": min(900.0, max(10.0, float(v.get("timeout") or 180))), "max_shots": min(12, max(1, int(v.get("max_shots") or 6)))}
    except (TypeError, ValueError):
        return None


def save(project: Path, state_dir: str, protect: list[str], shadow: bool, visual: dict | None = None) -> dict:
    clean = [_check_pattern(str(p)) for p in protect if str(p).strip()]  # raises ValueError on a bad pattern
    pol = {"protect": list(dict.fromkeys(clean)), "shadow": bool(shadow), "visual": _visual(visual)}
    p = path_of(project, state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(pol, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    return pol
