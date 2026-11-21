"""Overnight auto-fix: take the findings of an analysis and fix them one after another, unattended.

Every fix is a normal Solomon run (plan -> build -> scope guard -> tests -> cross review -> double check) that
starts from the result of the previous fix. Passed fixes are committed to a NEW branch `solomon/autofix-<id>`
under the identity "Solomon". The user's checked-out branch and working tree are never touched: in the morning
they read report.md, look at the branch (or combined.patch) and decide what to apply.
"""

from __future__ import annotations

import dataclasses
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import policy as policy_mod, scope_guard, quota as quota_mod, gitops
from .analysis import SEVERITY_RANK
from .orchestrator import Orchestrator
from .settings import ConfigError, Settings

MAX_CONSECUTIVE_FAILURES = 3
ITEM_FILES = 8  # a single overnight fix may touch at most this many files ...
ITEM_LINES = 400  # ... and this many diff lines (stricter than a hand-started run)
DEFAULT_BUDGET_MIN = 360.0
DEFAULT_ITEM_MIN = 45.0
ITEM_ATTEMPTS = 12  # a single overnight item is abandoned after this many attempts (a backstop behind repeated-failure detection)


class AutofixError(RuntimeError):
    pass


GROUP_MAX = 3  # findings in one file fixed together in a single run (one plan, one review); bigger bundles exceed the diff limit and fail more


@dataclass
class Item:
    cluster: int
    title: str
    file: str
    severity: str
    clusters: list[int] = field(default_factory=list)  # every finding this item fixes (several when grouped by file)
    status: str = "PENDING"  # PENDING | PASSED | FAILED | SKIPPED | ERROR | BLOCKED_QUOTA | STOPPED
    summary: str = ""
    run_id: str = ""
    attempts: int = 0
    commit: str = ""


@dataclass
class BatchResult:
    batch_id: str
    status: str  # DONE | PARTIAL | NOTHING | BLOCKED_QUOTA | STOPPED | ERROR
    summary: str
    branch: str = ""
    batch_dir: str = ""
    items: list[Item] = field(default_factory=list)
    report_path: str = ""
    patch_path: str = ""


def load_analysis(project: Path, state_dir: str, analysis_id: Optional[str] = None) -> dict:
    root = project / state_dir / "analyses"
    if not root.is_dir():
        raise AutofixError("이 프로젝트에는 분석 결과가 없습니다. 먼저 분석을 실행하세요.")
    if analysis_id:
        d = root / analysis_id
    else:
        done = sorted((x for x in root.iterdir() if (x / "analysis.json").is_file()), reverse=True)
        if not done:
            raise AutofixError("끝난 분석이 없습니다. 먼저 분석을 실행하세요.")
        d = done[0]
    f = d / "analysis.json"
    if not f.is_file():
        raise AutofixError(f"분석 결과를 찾을 수 없습니다: {d.name}")
    data = json.loads(f.read_text(encoding="utf-8"))
    data.setdefault("analysis_id", d.name)
    return data


def select_findings(analysis: dict, min_severity: str = "medium", min_agreement: int = 1,
                    verified_only: bool = True, max_fixes: int = 5, ids: Optional[list[int]] = None) -> list[dict]:
    """The findings worth fixing unattended: serious enough, evidence confirmed, enough AIs agree. Best first.
    `ids` = the findings a person ticked: exactly those, in report order (the filters do not apply). max_fixes 0 = no cap."""
    if ids is not None:
        want = {int(i) for i in ids}
        picked = [c for c in analysis.get("clusters", []) if c.get("id") in want and c.get("file")]
        return picked[:max_fixes] if max_fixes else picked
    floor = SEVERITY_RANK[min_severity]
    picked = []
    for c in analysis.get("clusters", []):
        if SEVERITY_RANK.get(c.get("severity"), 0) < floor:
            continue
        if c.get("agreement", 0) < min_agreement:
            continue
        if verified_only and not c.get("verified"):
            continue
        if not c.get("file"):
            continue
        picked.append(c)
    return picked[:max_fixes] if max_fixes else picked


def build_goal(c: dict) -> str:
    m = c["members"][0]
    where = f"{c['file']} (lines {c['line_start']}-{c['line_end']})"
    quote = next((x["quote"] for x in c["members"] if x.get("quote")), "")
    suggestions = [f"- ({x['ai']}) {x['suggestion']}" for x in c["members"] if x.get("suggestion")]
    return (
        f"Fix this one problem found by code analysis. Fix ONLY this problem.\n"
        f"Problem: {m['title']}\nWhere: {where} -- line numbers may have shifted, locate it by this quote:\n  {quote}\n"
        f"Details: {m['problem']}\n" + ("Suggested fixes:\n" + "\n".join(suggestions) + "\n" if suggestions else "") +
        "Rules: keep the change minimal; do not refactor or reformat unrelated code; do not change behaviour that is not "
        "part of the problem; add or update a test for the fix when the project has tests; never touch secrets, .env files, "
        "credentials, or deployment/trading configuration. If the problem turns out not to exist, make no change."
    )


def group_by_file(chosen: list[dict]) -> list[list[dict]]:
    """Findings in the same file become one unit of work (order of first appearance kept, at most GROUP_MAX each)."""
    groups: dict[str, list[list[dict]]] = {}
    order: list[list[dict]] = []
    for c in chosen:
        lists = groups.setdefault(c["file"], [])
        if not lists or len(lists[-1]) >= GROUP_MAX:
            lists.append([])
            order.append(lists[-1])
        lists[-1].append(c)
    return order


def build_group_goal(cs: list[dict]) -> str:
    if len(cs) == 1:
        return build_goal(cs[0])
    parts = []
    for n, c in enumerate(cs, 1):
        m = c["members"][0]
        quote = next((x["quote"] for x in c["members"] if x.get("quote")), "")
        sug = next((x["suggestion"] for x in c["members"] if x.get("suggestion")), "")
        parts.append(f"{n}. {m['title']} (lines {c['line_start']}-{c['line_end']})\n   quote: {quote}\n   details: {m['problem']}"
                     + (f"\n   suggested fix: {sug}" if sug else ""))
    return (f"Fix these {len(cs)} problems found by code analysis in {cs[0]['file']}. Fix ONLY these problems, all of them.\n"
            + "\n".join(parts) + "\nRules: keep every change minimal; do not refactor or reformat unrelated code; do not change behaviour "
            "that is not part of a problem; add or update a test for each fix when the project has tests; never touch secrets, .env files, "
            "credentials, or deployment/trading configuration. If a problem turns out not to exist, leave it alone and say so.")


def run_autofix(
    settings: Settings,
    project: Path,
    analysis_id: Optional[str] = None,
    *,
    min_severity: str = "medium",
    min_agreement: int = 1,
    verified_only: bool = True,
    max_fixes: int = 5,
    budget_minutes: float = DEFAULT_BUDGET_MIN,
    item_minutes: float = DEFAULT_ITEM_MIN,
    allow_untested: bool = False,
    ids: Optional[list[int]] = None,
    group: bool = False,
    orchestrator: Optional[Orchestrator] = None,
    clock: Callable[[], float] = time.time,
) -> BatchResult:
    project = Path(project).resolve()
    gitops.ensure_repo(project)
    if min_severity not in SEVERITY_RANK:
        raise AutofixError(f"min_severity must be one of {sorted(SEVERITY_RANK)}")
    if not settings.test_command and not allow_untested:
        raise AutofixError("테스트 명령이 없으면 밤새 자동 수정을 하지 않습니다 (검증 없는 수정이 쌓입니다). "
                           "테스트 명령을 설정하거나 --allow-untested 를 주세요.")
    analysis = load_analysis(project, settings.state_dir, analysis_id)
    chosen = select_findings(analysis, min_severity, min_agreement, verified_only, 0 if group else max_fixes, ids)
    # findings that sit in a PROTECTED file cannot be fixed by an AI at all: every attempt would end in "protected path was modified".
    # They are listed as skipped (a person fixes them) instead of burning an hour of attempts that can never pass.
    protect = policy_mod.load(project, settings.state_dir)["protect"]
    blocked = [c for c in chosen if scope_guard.is_protected(c["file"], protect)]
    chosen = [c for c in chosen if c not in blocked]
    units = group_by_file(chosen) if group else [[c] for c in chosen]
    if group and max_fixes:
        units = units[:max_fixes]

    batch_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    bdir = project / settings.state_dir / "batches" / batch_id
    bdir.mkdir(parents=True)
    gitops.exclude_from_git(project, settings.state_dir)
    branch = f"solomon/autofix-{batch_id}"
    result = BatchResult(batch_id, "NOTHING", "", branch, str(bdir),
                         [Item(u[0]["id"], u[0]["members"][0]["title"] if len(u) == 1 else f"{len(u)}건 묶음: {u[0]['members'][0]['title']} 외",
                               u[0]["file"], max((c["severity"] for c in u), key=lambda v: SEVERITY_RANK[v]), [c["id"] for c in u])
                          for u in units])
    for c in blocked:
        result.items.append(Item(c["id"], c["members"][0]["title"], c["file"], c["severity"], [c["id"]], status="SKIPPED",
                                 summary="보호 경로의 파일이라 AI가 고치지 않습니다 — 사람이 직접 고치거나 보호 경로에서 뺀 뒤 다시 실행하세요"))
    events = bdir / "events.jsonl"

    def log(kind: str, **fields) -> None:
        with events.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(clock())), "event": kind, **fields}, ensure_ascii=False) + "\n")

    log("batch_start", analysis=analysis.get("analysis_id"), items=[dataclasses.asdict(i) for i in result.items],
        branch=branch, budget_minutes=budget_minutes)
    if not units:
        result.summary = (f"선택한 {len(blocked)}건이 모두 보호 경로의 파일이라 AI가 고치지 않았습니다 (사람이 직접 고치거나 보호 경로에서 뺀 뒤 다시 실행하세요)."
                          if blocked else "조건에 맞는 고칠 항목이 없습니다 (심각도·근거 확인·AI 동의 기준).")
        return _finish(result, project, bdir, "HEAD", log)

    base = gitops.resolve(project, "HEAD")
    snapshot_note = ""
    if settings.include_uncommitted and gitops.is_dirty(project):
        # the batch starts from the folder as it is on disk (secrets excluded), exactly like a single run: otherwise every
        # file the person has edited since the last commit makes the finished patch fail to apply ("patch failed: file:3")
        try:
            base, kept = gitops.snapshot_worktree(project)
            snapshot_note = f"커밋 안 된 변경 {len(kept)}개 포함"
            log("snapshot", files=kept[:80], count=len(kept))
        except gitops.GitError as exc:
            log("snapshot_failed", error=str(exc)[:200])
    gitops.create_branch(project, branch, base)
    if orchestrator is None:
        s = dataclasses.replace(settings, roles=dict(settings.roles), max_attempts=ITEM_ATTEMPTS,
                                max_files_changed=min(settings.max_files_changed, ITEM_FILES),
                                max_diff_lines=min(settings.max_diff_lines, ITEM_LINES))
        orchestrator = Orchestrator(s, quota_board=quota_mod.QuotaBoard.persistent())
    orch = orchestrator
    started = clock()
    failures = 0
    stop: Optional[str] = None

    queue = list(zip(result.items, units))
    n = 0
    while queue:
        item, unit = queue.pop(0)
        n += 1
        left = budget_minutes - (clock() - started) / 60
        if left <= 1:
            stop = "시간 예산을 다 써서 남은 항목은 건너뛰었습니다"
            break
        orch.s.max_run_minutes = min(item_minutes, left)
        orch.s.baseline_check = bool(settings.baseline_check) and n == 1  # the test setup is judged once, on the original code
        log("item_start", n=n, cluster=item.cluster, title=item.title)

        def commit(wt: Path, res, item=item) -> str:
            sha = gitops.commit_all(wt, f"autofix: {item.title[:70]}\n\nFinding(s) #{', #'.join(str(i) for i in item.clusters or [item.cluster])} in {item.file} "
                                        f"({item.severity}). Batch {batch_id}.")
            gitops.move_branch(project, branch, sha)
            return sha

        try:
            res = orch.run(build_group_goal(unit), project, base_ref=branch, on_pass=commit)
        except Exception as exc:  # one broken item must not end the night
            item.status, item.summary = "ERROR", f"{type(exc).__name__}: {exc}"
            log("item_end", n=n, status="ERROR", summary=item.summary)
            failures += 1
        else:
            item.status, item.summary, item.run_id = res.status, res.summary, res.run_id
            item.attempts, item.commit = res.attempts, res.commit or ""
            log("item_end", n=n, status=res.status, attempts=res.attempts, summary=res.summary, commit=item.commit)
            failures = 0 if res.status == "PASSED" else failures + 1
            if res.status == "FAILED" and len(unit) > 1:  # a bundle that could not be fixed together: each problem gets its own chance
                singles = [Item(c["id"], c["members"][0]["title"], c["file"], c["severity"], [c["id"]]) for c in unit]
                item.summary += " → 묶음이 안 되어 문제를 하나씩 따로 다시 시도합니다"
                result.items.extend(singles)
                queue[0:0] = [(it, [c]) for it, c in zip(singles, unit)]
                log("item_split", n=n, into=[it.cluster for it in singles])
            if res.status in ("BLOCKED_QUOTA", "STOPPED"):
                stop = {"BLOCKED_QUOTA": "구독 사용 한도에 도달해 멈췄습니다 (API 결제로 넘어가지 않습니다)",
                        "STOPPED": "사용자가 중지했습니다"}[res.status]
                result.status = res.status
                break
            if res.status == "ERROR" and n == 1 and "수정하기 전" in res.summary:
                stop = "수정 전 코드에서도 테스트가 실패해 중단했습니다 (테스트 환경을 먼저 고치세요)"
                result.status = "ERROR"
                break
        if failures >= MAX_CONSECUTIVE_FAILURES:
            stop = f"{MAX_CONSECUTIVE_FAILURES}건 연속 실패해 멈췄습니다 (남은 항목은 같은 문제일 가능성이 큽니다)"
            break

    for item in result.items:
        if item.status == "PENDING":
            item.status = "SKIPPED"
    passed = sum(1 for i in result.items if i.status == "PASSED")
    if result.status == "NOTHING":
        result.status = "DONE" if passed == len(result.items) else "PARTIAL" if passed else "FAILED"
    result.summary = f"{len(result.items)}건 중 {passed}건 수정" + (f" · {stop}" if stop else "") + (f" · {snapshot_note}" if snapshot_note else "")
    return _finish(result, project, bdir, base, log, tip=branch if passed else None)


def run_task_chain(
    settings: Settings,
    project: Path,
    report_id: str,
    task_ids: Optional[list[int]] = None,
    *,
    budget_minutes: float = DEFAULT_BUDGET_MIN,
    item_minutes: float = DEFAULT_ITEM_MIN,
    orchestrator: Optional[Orchestrator] = None,
    clock: Callable[[], float] = time.time,
) -> BatchResult:
    """The tasks of a report, built one after another unattended: each is a normal Solomon run that starts from the result of the one before,
    passed ones are committed to a NEW branch (the checked-out branch and folder are never touched), and a person applies the result later.
    A task whose prerequisite did not pass (or was never applied) is skipped, independent tasks go on, three failures in a row stop the night."""
    from . import report as report_mod

    project = Path(project).resolve()
    gitops.ensure_repo(project)
    if not settings.test_command:
        raise AutofixError("테스트 명령이 없으면 자동 실행을 하지 않습니다 (검증 없는 변경이 쌓입니다). 프로젝트 설정에서 테스트 명령을 정하세요.")
    data = report_mod.load_tasks(project, settings.state_dir, report_id)
    if not data:
        raise AutofixError("작업 목록이 없습니다. 먼저 보고서에서 '작은 작업으로 나누기'를 하세요.")
    tasks = {t["id"]: t for t in data["tasks"]}
    status = report_mod.task_statuses(project, settings.state_dir, report_id, data["tasks"])
    applied = {i for i, st in status.items() if st["state"] == "applied"}
    wanted = [i for i in (task_ids or [t["id"] for t in data["tasks"]]) if i in tasks and i not in applied and status[i]["state"] != "running"]
    if not wanted:
        raise AutofixError("실행할 작업이 없습니다 (모두 적용되었거나 선택한 작업이 없습니다).")
    wanted.sort()

    batch_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    bdir = project / settings.state_dir / "batches" / batch_id
    bdir.mkdir(parents=True)
    gitops.exclude_from_git(project, settings.state_dir)
    branch = f"solomon/chain-{batch_id}"
    result = BatchResult(batch_id, "NOTHING", "", branch, str(bdir),
                         [Item(i, f"단계 {i}: {tasks[i]['title']}", ", ".join(tasks[i].get("files") or [])[:90], "medium", [i]) for i in wanted])
    events = bdir / "events.jsonl"

    def log(kind: str, **fields) -> None:
        with events.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(clock())), "event": kind, **fields}, ensure_ascii=False) + "\n")

    log("batch_start", report=report_id, items=[dataclasses.asdict(i) for i in result.items], branch=branch, budget_minutes=budget_minutes)
    base = gitops.resolve(project, "HEAD")
    snapshot_note = ""
    if settings.include_uncommitted and gitops.is_dirty(project):
        try:
            base, kept = gitops.snapshot_worktree(project)
            snapshot_note = f"커밋 안 된 변경 {len(kept)}개 포함"
            log("snapshot", files=kept[:80], count=len(kept))
        except gitops.GitError as exc:
            log("snapshot_failed", error=str(exc)[:200])
    gitops.create_branch(project, branch, base)
    if orchestrator is None:
        sv = dataclasses.replace(settings, roles=dict(settings.roles), max_attempts=ITEM_ATTEMPTS,
                                 max_files_changed=min(settings.max_files_changed, ITEM_FILES), max_diff_lines=min(settings.max_diff_lines, ITEM_LINES))
        orchestrator = Orchestrator(sv, quota_board=quota_mod.QuotaBoard.persistent())
    orch = orchestrator
    started = clock()
    failures = 0
    stop: Optional[str] = None
    done_here: set[int] = set()
    first = True
    for n, item in enumerate(result.items, 1):
        tid = item.cluster
        left = budget_minutes - (clock() - started) / 60
        if left <= 1:
            stop = "시간 예산을 다 써서 남은 단계는 건너뛰었습니다"
            break
        missing = [d for d in tasks[tid].get("depends_on", []) if d not in done_here and d not in applied]
        if missing:
            item.status = "SKIPPED"
            item.summary = "먼저 끝나야 할 단계 " + ", ".join(str(d) for d in missing) + " 가 통과하지 못했거나 적용되지 않아 건너뜁니다"
            log("item_skipped", n=n, task=tid, missing=missing)
            continue
        orch.s.max_run_minutes = min(item_minutes, left)
        orch.s.baseline_check = bool(settings.baseline_check) and first  # the test setup is judged once, on the original code
        first = False
        log("item_start", n=n, task=tid, cluster=tid, title=item.title)

        def commit(wt: Path, res, item=item, tid=tid) -> str:
            sha = gitops.commit_all(wt, f"report task {tid}: {tasks[tid]['title'][:70]}\n\nReport {report_id}, chain {batch_id}.")
            gitops.move_branch(project, branch, sha)
            return sha

        try:
            res = orch.run(report_mod.task_goal_text(project, settings.state_dir, report_id, tid), project, base_ref=branch, on_pass=commit)
        except Exception as exc:  # one broken step must not end the night
            item.status, item.summary = "ERROR", f"{type(exc).__name__}: {exc}"
            log("item_end", n=n, task=tid, status="ERROR", summary=item.summary)
            failures += 1
        else:
            item.status, item.summary, item.run_id = res.status, res.summary, res.run_id
            item.attempts, item.commit = res.attempts, res.commit or ""
            log("item_end", n=n, task=tid, status=res.status, attempts=res.attempts, summary=res.summary, commit=item.commit)
            if res.status == "PASSED":
                done_here.add(tid)
                failures = 0
            else:
                failures += 1
            if res.status in ("BLOCKED_QUOTA", "STOPPED"):
                stop = {"BLOCKED_QUOTA": "구독 사용 한도에 도달해 멈췄습니다 (API 결제로 넘어가지 않습니다)", "STOPPED": "사용자가 중지했습니다"}[res.status]
                result.status = res.status
                break
            if res.status == "ERROR" and not done_here and "수정하기 전" in res.summary:
                stop = "수정 전 코드에서도 테스트가 실패해 중단했습니다 (테스트 환경을 먼저 고치세요)"
                result.status = "ERROR"
                break
        if failures >= MAX_CONSECUTIVE_FAILURES:
            stop = f"{MAX_CONSECUTIVE_FAILURES}단계 연속 실패해 멈췄습니다"
            break
    for item in result.items:
        if item.status == "PENDING":
            item.status = "SKIPPED"
            item.summary = item.summary or (stop or "실행하지 않았습니다")
    passed = sum(1 for i in result.items if i.status == "PASSED")
    if result.status == "NOTHING":
        result.status = "DONE" if passed == len(result.items) else "PARTIAL" if passed else "FAILED"
    result.summary = f"{len(result.items)}단계 중 {passed}단계 통과" + (f" · {stop}" if stop else "") + (f" · {snapshot_note}" if snapshot_note else "")
    return _finish(result, project, bdir, base, log, tip=branch if passed else None)


def _finish(result: BatchResult, project: Path, bdir: Path, base: str, log, tip: Optional[str] = None) -> BatchResult:
    if tip:
        patch = bdir / "combined.patch"
        patch.write_text(gitops.diff_between(project, base, tip), encoding="utf-8")
        result.patch_path = str(patch)
    report = bdir / "report.md"
    report.write_text(render_report(result, project), encoding="utf-8")
    result.report_path = str(report)
    (bdir / "batch.json").write_text(json.dumps(dataclasses.asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    log("batch_end", status=result.status, summary=result.summary)
    return result


_MARK = {"PASSED": "✅ 수정됨", "FAILED": "❌ 실패", "ERROR": "⚠ 오류", "SKIPPED": "⏭ 건너뜀", "BLOCKED_QUOTA": "⛔ 한도",
         "STOPPED": "⏹ 중지", "PENDING": "…"}


def render_report(r: BatchResult, project: Path) -> str:
    lines = [f"# 밤샘 자동 수정 결과 ({r.batch_id})", "", f"- 결과: **{r.status}** — {r.summary}", f"- 수정 브랜치: `{r.branch}`", ""]
    for i, it in enumerate(r.items, 1):
        lines.append(f"{i}. {_MARK.get(it.status, it.status)} [{it.severity}] {it.title} — `{it.file}`"
                     + (f" (시도 {it.attempts}번, 커밋 {it.commit[:8]})" if it.commit else ""))
        if it.status != "PASSED" and it.summary:
            lines.append(f"   - {it.summary.splitlines()[0][:200]}")
    if r.patch_path:
        lines += ["", "## 아침에 확인하는 법", "",
                  f"1. 변경 내용 보기: `git -C {project} log --stat {r.branch}` / `git -C {project} diff HEAD {r.branch}`",
                  f"2. 마음에 들면 작업 폴더에 적용(커밋은 안 함): `git -C {project} apply {r.patch_path}`",
                  f"3. 일부만 원하면 커밋 단위로: `git -C {project} cherry-pick <커밋>`",
                  f"4. 모두 버리기: `git -C {project} branch -D {r.branch}` (원본은 바뀌지 않았습니다)", "",
                  "라이브로 돌아가는 프로그램이면 **적용 전에 직접 테스트**하고, 장 마감 후에 반영하세요."]
    return "\n".join(lines) + "\n"


def list_batches(project: Path, state_dir: str, limit: int = 5) -> list[dict]:
    """Recent batches for the dashboard, from batch.json when finished, else rebuilt from events.jsonl (running/interrupted)."""
    root = Path(project) / state_dir / "batches"
    if not root.is_dir():
        return []
    rows = []
    for d in sorted(root.iterdir(), reverse=True)[:limit]:
        f = d / "batch.json"
        if f.is_file():
            try:
                row = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                continue
            row["finished"] = True
        else:
            ev = d / "events.jsonl"
            if not ev.is_file():
                continue
            row = {"batch_id": d.name, "status": "RUNNING", "summary": "", "branch": "", "items": [], "finished": False}
            for line in ev.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("event") == "batch_start":
                    row["items"], row["branch"] = e.get("items", []), e.get("branch", "")
                elif e.get("event") == "item_end" and 0 < e.get("n", 0) <= len(row["items"]):
                    row["items"][e["n"] - 1].update(status=e.get("status"), summary=e.get("summary", ""), attempts=e.get("attempts", 0))
        row["batch_id"] = d.name
        row["applied"] = (d / "applied.json").is_file()
        row["has_patch"] = (d / "combined.patch").is_file()
        rows.append(row)
    return rows
