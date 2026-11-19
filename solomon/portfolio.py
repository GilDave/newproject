"""Portfolio view: one card of numbers per project, plus per-AI performance, all derived from the run logs on disk.

Nothing here is estimated by an AI. It counts what the pipeline recorded: calls, seconds, tokens, verdicts, outcomes."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from . import gitops, manage

ROLE_BASES = ("planner", "builder", "reviewer")


def _events(path: Path) -> list[dict]:
    out = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


def _ts(value: object) -> Optional[float]:
    try:
        return time.mktime(time.strptime(str(value), "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return None


def _ai(table: dict, name: str) -> dict:
    return table.setdefault(name, {"calls": 0, "failures": 0, "sec": 0.0, "tokens": 0, "roles": {},
                                   "reviews": {"pass": 0, "fail": 0}, "builds": {"attempts": 0, "passed": 0}, "plans": 0, "advice": 0,
                                   "decisions": {"ADOPT": 0, "REJECT": 0, "NEED_HUMAN": 0}})


def run_facts(events: list[dict], ais: dict) -> dict:
    """One run's outcome, and its contribution to the per-AI table (mutated)."""
    status, attempts, started, ended, goal = "RUNNING", 0, None, None, ""
    builders: dict[int, str] = {}
    wrote: dict[str, bool] = {}
    cur = 0
    last_builder = ""
    arb_final = ""
    for e in events:
        k = e.get("event")
        if k == "run_start":
            goal, started = str(e.get("goal", "")), _ts(e.get("ts"))
        elif k == "attempt_start":
            cur = int(e.get("attempt") or cur + 1)
        elif k == "call_start":
            wrote[str(e.get("role"))] = bool(e.get("write"))
        elif k == "call_end":
            role, prov = str(e.get("role")), str(e.get("provider"))
            a = _ai(ais, prov)
            a["calls"] += 1
            a["sec"] += float(e.get("duration_sec") or 0)
            tok = e.get("tokens") or {}
            est = e.get("tokens_est") or {}
            a["tokens"] += int(tok.get("total") or (int(est.get("in") or 0) + int(est.get("out") or 0)))
            a["roles"][role] = a["roles"].get(role, 0) + 1
            if role.rstrip("23") == "planner" and role == "planner":
                a["plans"] += 1
            if role.rstrip("23") == "builder" and not wrote.get(role):
                a["advice"] += 1
        elif k == "call_error":
            _ai(ais, str(e.get("provider")))["failures"] += 1
        elif k == "builder_pick":
            builders[int(e.get("attempt") or cur)] = last_builder = str(e.get("provider"))
            _ai(ais, last_builder)["builds"]["attempts"] += 1
        elif k == "review_role" and not e.get("error"):
            _ai(ais, str(e.get("provider")))["reviews"]["pass" if e.get("verdict") == "PASS" else "fail"] += 1
        elif k == "arbiter":
            fin = str(e.get("final"))
            if fin in ("ADOPT", "REJECT", "NEED_HUMAN"):
                _ai(ais, str(e.get("provider")))["decisions"][fin] += 1
                arb_final = fin
        elif k == "run_end":
            status, attempts, ended = str(e.get("status")), int(e.get("attempts") or cur), _ts(e.get("ts"))
    if status == "PASSED" and builders.get(attempts):
        _ai(ais, builders[attempts])["builds"]["passed"] += 1
    return {"arbiter_final": arb_final, "status": status, "attempts": attempts, "goal": goal, "started": started,
            "minutes": (ended - started) / 60 if started and ended else None}


def _newest(root: Path) -> float:
    best = 0.0
    try:
        for d in root.iterdir():
            for f in (d / "events.jsonl", d / "batch.json"):
                try:
                    best = max(best, f.stat().st_mtime)
                except OSError:
                    pass
    except OSError:
        pass
    return best


def card(project: Path, state_dir: str, *, is_git: bool, managed: bool, test_command: str, busy: bool) -> dict:
    root = Path(project) / state_dir
    ais: dict = {}
    runs: list[dict] = []
    if (root / "runs").is_dir():
        def order(d: Path):  # the id starts with the second it began; runs in the same second are told apart by when they were last written
            try:
                return (d.name[:15], (d / "events.jsonl").stat().st_mtime_ns)
            except OSError:
                return (d.name[:15], 0)

        for d in sorted((root / "runs").iterdir(), key=order):
            ev = _events(d / "events.jsonl")
            if ev:
                f = run_facts(ev, ais)
                f["id"] = d.name
                runs.append(f)
    done = [r for r in runs if r["status"] != "RUNNING"]
    passed = [r for r in done if r["status"] == "PASSED"]
    minutes = [r["minutes"] for r in passed if r["minutes"] is not None]

    analyses = []
    if (root / "analyses").is_dir():
        for d in sorted((root / "analyses").iterdir()):
            f = d / "analysis.json"
            if f.is_file():
                try:
                    a = json.loads(f.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                cl = a.get("clusters", [])
                analyses.append({"id": d.name, "findings": len(cl), "verified": sum(1 for c in cl if c.get("verified")),
                                 "ais": list((a.get("ais") or {}).keys())})
                for name, info in (a.get("ais") or {}).items():
                    row = _ai(ais, name)
                    row["roles"]["analysis"] = row["roles"].get("analysis", 0) + 1
                    row["calls"] += 1
                    row["sec"] += float(info.get("duration_sec") or 0)
                    tk = info.get("tokens")
                    row["tokens"] += int(tk.get("total") or 0) if isinstance(tk, dict) else 0
                    if not info.get("ok", True):
                        row["failures"] += 1
    videos = 0
    if (root / "videos").is_dir():
        for d in sorted((root / "videos").iterdir()):
            f = d / "analysis.json"
            if f.is_file():
                try:
                    a = json.loads(f.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                videos += 1
                for name, info in (a.get("ais") or {}).items():
                    row = _ai(ais, name)
                    row["roles"]["video"] = row["roles"].get("video", 0) + 1
                    row["calls"] += 1
                    row["sec"] += float(info.get("duration_sec") or 0)
                    tk = info.get("tokens")
                    row["tokens"] += int(tk.get("total") or (int(tk.get("in") or 0) + int(tk.get("out") or 0))) if isinstance(tk, dict) else 0
                    if not info.get("ok", True):
                        row["failures"] += 1
    batches = []
    if (root / "batches").is_dir():
        for d in sorted((root / "batches").iterdir()):
            b = manage._read_json(d / "batch.json")
            if b:
                batches.append({"id": d.name, "status": b.get("status"), "fixed": sum(1 for i in b.get("items", []) if i.get("status") == "PASSED")})

    marks = manage._load_marks(Path(project), state_dir) if root.is_dir() else {}
    # the arbiter's scorecard: what it decided on results that reached a person, against what the person then did
    score = {"adopt": {"accepted": 0, "discarded": 0, "pending": 0}, "need_human": {"accepted": 0, "discarded": 0, "pending": 0}}
    for r in runs:
        if r["status"] != "PASSED" or r.get("arbiter_final") not in ("ADOPT", "NEED_HUMAN"):
            continue
        st = manage._state_of(f"run:{r['id']}", marks, (root / "runs" / r["id"] / "applied.json").is_file())
        bucket = "accepted" if st in ("applied", "committed") else "discarded" if st == "discarded" else "pending"
        score["adopt" if r["arbiter_final"] == "ADOPT" else "need_human"][bucket] += 1
    pending = manage.list_inbox(Path(project), state_dir)["pending"] if root.is_dir() and is_git else 0
    committed = sum(1 for v in marks.values() if v.get("state") == "committed")
    last = max(_newest(root / "runs"), _newest(root / "analyses"), _newest(root / "batches")) if root.is_dir() else 0.0

    branch, dirty = "", 0
    if is_git:
        branch = gitops.git(["rev-parse", "--abbrev-ref", "HEAD"], project, check=False).stdout.strip()
        dirty = len([l for l in gitops.git(["status", "--porcelain"], project, check=False).stdout.splitlines() if l.strip()])

    last_an = analyses[-1] if analyses else None
    if busy:
        nxt = {"label": "작업 진행 중 — 작업실에서 보기", "action": "work"}
    elif pending:
        nxt = {"label": f"결정 대기 {pending}건 — 확인하고 적용", "action": "manage"}
    elif not analyses and not runs:
        nxt = {"label": "처음이네요 — 분석부터 해 보세요", "action": "analyze"}
    elif last_an and last_an["findings"] and not batches:
        nxt = {"label": f"분석에서 {last_an['findings']}건 발견 — 밤샘 자동 수정", "action": "autofix"}
    else:
        nxt = {"label": "다음 개선 시작", "action": "improve"}

    return {
        "counts": {"runs": len(runs), "passed": len(passed), "failed": sum(1 for r in done if r["status"] in ("FAILED", "ERROR")),
                   "other": sum(1 for r in done if r["status"] in ("BLOCKED_QUOTA", "STOPPED")), "analyses": len(analyses), "videos": videos,
                   "batches": len(batches), "pending": pending, "committed": committed,
                   "running": sum(1 for r in runs if r["status"] == "RUNNING")},
        "pass_rate": round(len(passed) / len(done) * 100) if done else None,
        "avg_attempts": round(sum(r["attempts"] for r in passed) / len(passed), 1) if passed else None,
        "avg_minutes": round(sum(minutes) / len(minutes), 1) if minutes else None,
        "tokens": sum(a["tokens"] for a in ais.values()),
        "recent": [r["status"] for r in runs[-14:]],
        "last_run": ({"id": runs[-1]["id"], "status": runs[-1]["status"], "goal": manage.short_goal(runs[-1]["goal"])[:90]} if runs else None),
        "last_analysis": last_an, "batches": batches[-3:], "ais": ais, "arbiter": score,
        "last_activity": time.strftime("%Y-%m-%d %H:%M", time.localtime(last)) if last else "",
        "branch": branch, "dirty": dirty, "is_git": is_git, "managed": managed, "test_command": test_command, "busy": busy, "next": nxt,
    }


def merge_ais(cards: list[dict]) -> dict:
    total: dict = {}
    for c in cards:
        for name, a in c["ais"].items():
            t = _ai(total, name)
            for k in ("calls", "failures", "sec", "tokens", "plans", "advice"):
                t[k] += a[k]
            for r, n in a["roles"].items():
                t["roles"][r] = t["roles"].get(r, 0) + n
            for k in ("pass", "fail"):
                t["reviews"][k] += a["reviews"][k]
            for k in ("attempts", "passed"):
                t["builds"][k] += a["builds"][k]
            for k, v in a["decisions"].items():
                t["decisions"][k] += v
    for a in total.values():
        a["avg_sec"] = round(a["sec"] / a["calls"], 1) if a["calls"] else None
    return total
