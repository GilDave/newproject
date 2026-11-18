"""The result inbox: everything Solomon produced that is waiting for a human decision, and the decisions.

A *changeset* is one of
  run:<run id>             the patch of a passed improvement run
  batch:<batch id>         all passed fixes of an overnight batch, as one patch
  item:<batch id>:<sha>    one fix of a batch (one commit on its branch)
and goes  pending -> applied (to the working folder, uncommitted) -> committed,
or pending -> discarded, or applied -> back to pending (reverted). Nothing here ever pushes or touches other files.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Optional

from . import gitops
from .status import summarize_run

RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
BATCH_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
SHA = re.compile(r"^[0-9a-f]{7,40}$")
MAX_VIEW = 300_000
_LOCK = threading.Lock()


class ManageError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


# ------------------------------------------------------------------ patch helpers
def patch_files(text: str) -> list[str]:
    files: list[str] = []
    for m in re.finditer(r"^diff --git a/(.+?) b/(.+)$", text, re.M):
        if m.group(2) not in files:
            files.append(m.group(2))
    return files


def patch_stats(text: str) -> tuple[int, int]:
    added = removed = 0
    for line in text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def _check(project: Path, patch: Path, reverse: bool = False) -> tuple[bool, str]:
    args = ["apply", "--check"] + (["-R"] if reverse else []) + [str(patch)]
    p = gitops.git(args, project, check=False)
    if p.returncode == 0:
        return True, ""
    return False, (p.stderr.strip().splitlines() or ["적용할 수 없습니다"])[0][:200]


def _dirty(project: Path) -> list[str]:
    out = gitops.git(["status", "--porcelain"], project).stdout.splitlines()
    return [l[3:].split(" -> ")[-1].strip('"') for l in out if len(l) > 3]


# ------------------------------------------------------------------ marks (decisions)
def _marks_path(project: Path, state_dir: str) -> Path:
    return project / state_dir / "inbox.json"


def _load_marks(project: Path, state_dir: str) -> dict:
    try:
        return json.loads(_marks_path(project, state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_marks(project: Path, state_dir: str, marks: dict) -> None:
    p = _marks_path(project, state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(marks, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _when(item_id: str) -> str:
    m = re.match(r"^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})", item_id)
    return f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}" if m else ""


# ------------------------------------------------------------------ locating things
def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def short_goal(goal: str) -> str:
    """An autofix prompt is long; what a person wants to see is its first line or the problem title."""
    m = re.search(r"^Problem: (.+)$", goal, re.M)
    if goal.startswith("Fix this one problem") and m:
        return "[자동 수정] " + m.group(1).strip()
    return goal.strip().splitlines()[0] if goal.strip() else ""


def _goal(run_dir: Path) -> str:
    try:
        first = (run_dir / "events.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()[0]
        return str(json.loads(first).get("goal", ""))
    except (OSError, ValueError, IndexError):
        return ""


def parse_key(key: str) -> tuple[str, str, str]:
    parts = (key or "").split(":")
    if len(parts) == 2 and parts[0] == "run" and RUN_ID.match(parts[1]):
        return "run", parts[1], ""
    if len(parts) == 2 and parts[0] == "batch" and BATCH_ID.match(parts[1]):
        return "batch", parts[1], ""
    if len(parts) == 3 and parts[0] == "item" and BATCH_ID.match(parts[1]) and SHA.match(parts[2]):
        return "item", parts[1], parts[2]
    raise ManageError(400, "항목 식별자가 올바르지 않습니다.")


def patch_path(project: Path, state_dir: str, key: str) -> Path:
    kind, ident, sha = parse_key(key)
    root = project / state_dir
    if kind == "run":
        p = root / "runs" / ident / "result.patch"
    elif kind == "batch":
        p = root / "batches" / ident / "combined.patch"
    else:
        batch = _read_json(root / "batches" / ident / "batch.json")
        full = next((i.get("commit") for i in batch.get("items", []) if i.get("commit", "").startswith(sha)), None)
        if not full:
            raise ManageError(404, "이 배치에 그 커밋이 없습니다.")
        p = root / "batches" / ident / f"item-{full[:10]}.patch"
        if not p.is_file():
            if gitops.git(["cat-file", "-e", full], project, check=False).returncode != 0:
                raise ManageError(404, "커밋을 찾을 수 없습니다 (브랜치를 지웠을 수 있습니다).")
            p.write_text(gitops.git(["diff", "--binary", "--no-renames", f"{full}^", full], project).stdout, encoding="utf-8")
    if not p.is_file():
        raise ManageError(404, "적용할 패치 파일이 없습니다.")
    return p


# ------------------------------------------------------------------ the inbox
def _state_of(key: str, marks: dict, legacy_applied: bool) -> str:
    st = (marks.get(key) or {}).get("state")
    if st in ("applied", "committed", "discarded"):
        return st
    return "applied" if legacy_applied else "pending"


def list_inbox(project: Path, state_dir: str, runs: int = 15, batches: int = 6) -> dict:
    project = Path(project)
    root = project / state_dir
    marks = _load_marks(project, state_dir)
    dirty = _dirty(project)
    dirty_set = set(dirty)
    entries: list[dict] = []

    def make(key: str, kind: str, title: str, ident: str, patch: Path, status: str, legacy: bool, extra: dict) -> dict:
        text = patch.read_text(encoding="utf-8", errors="replace")
        files = patch_files(text)
        added, removed = patch_stats(text)
        st = _state_of(key, marks, legacy)
        note = ""
        if st == "applied" and files and not (set(files) & dirty_set):
            st, note = "committed", "작업 폴더에 변경이 없어 커밋된 것으로 보입니다 (직접 커밋했거나 되돌린 경우)."
        e = {"key": key, "kind": kind, "id": ident, "title": title, "status": status, "when": _when(ident), "state": st,
             "note": note, "files": files, "added": added, "removed": removed,
             "commit": (marks.get(key) or {}).get("sha", ""), "overlap": sorted(set(files) & dirty_set)}
        if st == "pending":
            e["applicable"], e["reason"] = _check(project, patch)
        elif st == "applied":
            e["revertible"], e["reason"] = _check(project, patch, reverse=True)
        e.update(extra)
        return e

    owned: set[str] = set()  # runs made by an overnight batch are managed through the batch, not listed twice
    if (root / "batches").is_dir():
        for bd in (root / "batches").iterdir():
            owned |= {i.get("run_id") for i in _read_json(bd / "batch.json").get("items", []) if i.get("run_id")}
    run_root = root / "runs"
    if run_root.is_dir():
        for d in sorted(run_root.iterdir(), reverse=True):
            if d.name in owned:
                continue
            if len(entries) >= runs:
                break
            if (d / "result.patch").is_file() and RUN_ID.match(d.name):
                res = _read_json(d / "result.json")
                entries.append(make(f"run:{d.name}", "run", short_goal(_goal(d))[:120] or d.name, d.name, d / "result.patch",
                                    res.get("status", "PASSED"), (d / "applied.json").is_file(),
                                    {"summary": str(res.get("summary", ""))[:300], "attempts": res.get("attempts", 0)}))

    batch_root = root / "batches"
    if batch_root.is_dir():
        for d in sorted(batch_root.iterdir(), reverse=True)[:batches]:
            b = _read_json(d / "batch.json")
            if not BATCH_ID.match(d.name) or not b or not (d / "combined.patch").is_file():
                continue
            key = f"batch:{d.name}"
            items = []
            batch_entry = make(key, "batch", b.get("summary", "") or "밤샘 자동 수정", d.name, d / "combined.patch",
                               b.get("status", ""), (d / "applied.json").is_file(),
                               {"branch": b.get("branch", ""), "summary": b.get("summary", "")})
            for it in b.get("items", []):
                sha = it.get("commit") or ""
                ikey = f"item:{d.name}:{sha[:10]}" if sha else ""
                row = {"title": it.get("title", ""), "severity": it.get("severity", ""), "file": it.get("file", ""),
                       "status": it.get("status", ""), "summary": str(it.get("summary", ""))[:200],
                       "attempts": it.get("attempts", 0), "commit": sha[:10], "key": ikey}
                if ikey:
                    if batch_entry["state"] in ("applied", "committed", "discarded"):
                        row["state"] = batch_entry["state"]
                    else:
                        try:
                            ip = patch_path(project, state_dir, ikey)
                            row.update(make(ikey, "item", row["title"], d.name, ip, it.get("status", ""), False, {}))
                            row["title"], row["commit"], row["key"] = it.get("title", ""), sha[:10], ikey
                        except ManageError as exc:
                            row["state"], row["reason"] = "pending", exc.message
                            row["applicable"] = False
                items.append(row)
            batch_entry["items"] = items
            entries.append(batch_entry)

    entries.sort(key=lambda e: e["when"], reverse=True)
    head = gitops.git(["rev-parse", "--abbrev-ref", "HEAD"], project, check=False).stdout.strip()
    pending = sum(1 for e in entries if e["state"] in ("pending", "applied") and e["kind"] != "item")
    return {"entries": entries, "dirty": dirty[:60], "branch": head, "pending": pending}


# ------------------------------------------------------------------ actions
def read_diff(project: Path, state_dir: str, key: str) -> dict:
    p = patch_path(Path(project), state_dir, key)
    text = p.read_text(encoding="utf-8", errors="replace")
    return {"key": key, "text": text[:MAX_VIEW], "truncated": len(text) > MAX_VIEW, "files": patch_files(text)}


def _set(project: Path, state_dir: str, key: str, value: Optional[dict]) -> None:
    marks = _load_marks(project, state_dir)
    if value is None:
        marks.pop(key, None)
    else:
        marks[key] = value
    _save_marks(project, state_dir, marks)


def _legacy_marker(project: Path, state_dir: str, key: str) -> Optional[Path]:
    kind, ident, _ = parse_key(key)
    if kind == "run":
        return project / state_dir / "runs" / ident / "applied.json"
    if kind == "batch":
        return project / state_dir / "batches" / ident / "applied.json"
    return None


def _current(project: Path, state_dir: str, key: str) -> str:
    marker = _legacy_marker(project, state_dir, key)
    return _state_of(key, _load_marks(project, state_dir), bool(marker and marker.exists()))


def perform(project: Path, state_dir: str, key: str, action: str, message: str = "", busy: bool = False) -> dict:
    project = Path(project)
    kind, ident, _ = parse_key(key)
    with _LOCK:
        state = _current(project, state_dir, key)
        if action == "apply":
            if state != "pending":
                raise ManageError(409, {"applied": "이미 적용했습니다.", "committed": "이미 커밋했습니다.",
                                        "discarded": "폐기한 항목입니다."}.get(state, "적용할 수 없는 상태입니다."))
            if busy:
                raise ManageError(409, "이 프로젝트에서 작업이 진행 중입니다. 끝난 뒤 적용하세요.")
            _check_siblings(project, state_dir, key)
            p = patch_path(project, state_dir, key)
            try:
                gitops.apply_patch(project, p)
            except gitops.GitError as exc:
                raise ManageError(409, f"패치를 적용하지 못했습니다 (작업 폴더가 그사이 바뀌었거나 이미 반영돼 있을 수 있습니다): {exc}") from exc
            files = patch_files(p.read_text(encoding="utf-8", errors="replace"))
            _set(project, state_dir, key, {"state": "applied", "at": _now(), "files": files})
            marker = _legacy_marker(project, state_dir, key)
            if marker:
                marker.write_text(json.dumps({"applied_at": _now()}), encoding="utf-8")
            return {"ok": True, "message": f"작업 폴더에 적용했습니다 (파일 {len(files)}개). 커밋은 하지 않았습니다."}

        if action == "revert":
            if state != "applied":
                raise ManageError(409, "되돌릴 수 있는 것은 적용만 하고 커밋하지 않은 항목입니다.")
            p = patch_path(project, state_dir, key)
            ok, why = _check(project, p, reverse=True)
            if not ok:
                raise ManageError(409, f"되돌릴 수 없습니다 (적용 뒤 같은 파일을 더 고쳤을 수 있습니다): {why}")
            gitops.git(["apply", "-R", str(p)], project)
            _set(project, state_dir, key, None)
            marker = _legacy_marker(project, state_dir, key)
            if marker and marker.exists():
                marker.unlink()
            return {"ok": True, "message": "적용을 되돌렸습니다. 작업 폴더가 적용 전 상태입니다."}

        if action == "commit":
            if state != "applied":
                raise ManageError(409, "먼저 적용한 항목만 커밋할 수 있습니다.")
            message = (message or "").strip()
            if not message:
                raise ManageError(400, "커밋 메시지를 입력하세요.")
            p = patch_path(project, state_dir, key)
            files = patch_files(p.read_text(encoding="utf-8", errors="replace"))
            if not files:
                raise ManageError(409, "커밋할 파일이 없습니다.")
            if not gitops.git(["config", "user.email"], project, check=False).stdout.strip():
                raise ManageError(400, "git 사용자 정보가 없습니다. 터미널에서 git config --global user.name / user.email 을 한 번 설정하세요.")
            try:
                gitops.git(["add", "-A", "--", *files], project)
                gitops.git(["commit", "-m", message[:2000], "--", *files], project)
            except gitops.GitError as exc:
                raise ManageError(409, f"커밋하지 못했습니다: {exc}") from exc
            sha = gitops.git(["rev-parse", "--short", "HEAD"], project).stdout.strip()
            _set(project, state_dir, key, {"state": "committed", "at": _now(), "sha": sha, "files": files})
            return {"ok": True, "message": f"커밋했습니다 ({sha}). 이 항목의 파일만 커밋했고, 다른 변경은 건드리지 않았습니다.", "sha": sha}

        if action == "discard":
            if state != "pending":
                raise ManageError(409, "적용 전의 항목만 폐기할 수 있습니다. 적용한 것은 먼저 되돌리세요.")
            _set(project, state_dir, key, {"state": "discarded", "at": _now()})
            return {"ok": True, "message": "폐기함으로 옮겼습니다. 언제든 '복원'할 수 있고, 원본은 바뀌지 않았습니다."}

        if action == "restore":
            if state != "discarded":
                raise ManageError(409, "폐기한 항목만 복원할 수 있습니다.")
            if kind == "batch":
                b = _read_json(project / state_dir / "batches" / ident / "batch.json")
                br = b.get("branch", "")
                if br and gitops.git(["rev-parse", "--verify", "-q", f"refs/heads/{br}"], project, check=False).returncode != 0:
                    raise ManageError(409, "이 배치의 수정 브랜치가 없어 복원할 수 없습니다 (직접 삭제했을 수 있습니다).")
            _set(project, state_dir, key, None)
            return {"ok": True, "message": "복원했습니다. 다시 결과함에서 적용할 수 있습니다."}

        if action == "revert_commit":
            mark = _load_marks(project, state_dir).get(key) or {}
            sha = mark.get("sha", "")
            if state != "committed" or not sha:
                raise ManageError(409, "이 대시보드에서 커밋한 항목만 커밋을 취소할 수 있습니다 (직접 커밋한 것은 git으로 되돌리세요).")
            if gitops.git(["merge-base", "--is-ancestor", sha, "HEAD"], project, check=False).returncode != 0:
                raise ManageError(409, "그 커밋이 현재 브랜치에 없습니다 (브랜치를 바꿨거나 이미 되돌렸을 수 있습니다).")
            p = gitops.git(["revert", "--no-edit", sha], project, check=False)
            if p.returncode != 0:
                gitops.git(["revert", "--abort"], project, check=False)
                raise ManageError(409, "커밋을 취소하지 못했습니다 (그 뒤에 같은 파일이 더 바뀌었거나 작업 폴더에 겹치는 변경이 있습니다): "
                                       + (p.stderr.strip().splitlines() or [""])[-1][:200])
            _set(project, state_dir, key, None)
            marker = _legacy_marker(project, state_dir, key)
            if marker and marker.exists():
                marker.unlink()
            return {"ok": True, "message": "커밋을 취소했습니다 (취소 커밋이 새로 생겼고 기록은 지워지지 않습니다). 항목은 다시 '적용 전'입니다."}
    raise ManageError(400, "알 수 없는 동작입니다.")


def _check_siblings(project: Path, state_dir: str, key: str) -> None:
    """Whole-batch and single-item applies must not double up."""
    kind, ident, _ = parse_key(key)
    if kind not in ("batch", "item"):
        return
    marks = _load_marks(project, state_dir)
    legacy = (project / state_dir / "batches" / ident / "applied.json").is_file()
    batch_done = _state_of(f"batch:{ident}", marks, legacy) in ("applied", "committed")
    if kind == "item" and batch_done:
        raise ManageError(409, "이 배치 전체가 이미 적용돼 있습니다.")
    if kind == "batch":
        for k, v in marks.items():
            if k.startswith(f"item:{ident}:") and v.get("state") in ("applied", "committed"):
                raise ManageError(409, "이 배치의 일부 항목을 이미 적용했습니다. 남은 항목을 개별로 적용하세요.")


# ------------------------------------------------------------------ 5W1H: what happened under one id
STATUS_KO = {"PASSED": "통과", "FAILED": "실패", "ERROR": "오류", "BLOCKED_QUOTA": "구독 한도 소진", "STOPPED": "중지됨",
             "DONE": "완료", "PARTIAL": "일부 성공", "NOTHING": "대상 없음", "SKIPPED": "건너뜀"}
ROLE_KO = {"planner": "계획", "planner2": "계획 검토1", "planner3": "계획 검토2", "builder": "구현", "builder2": "구현 보조1", "builder3": "구현 보조2",
           "reviewer": "검수", "reviewer2": "2차 검수", "reviewer3": "3차 검수", "analysis": "분석"}


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


def _span(events: list[dict]) -> tuple[str, str, Optional[int]]:
    def ts(e):
        try:
            return time.mktime(time.strptime(str(e.get("ts")), "%Y-%m-%dT%H:%M:%S"))
        except (ValueError, TypeError):
            return None
    stamps = [t for t in (ts(e) for e in events) if t]
    if not stamps:
        return "", "", None
    f = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
    return f(stamps[0]), f(stamps[-1]), int(stamps[-1] - stamps[0])


def _tests_line(events: list[dict]) -> str:
    v = [e for e in events if e.get("event") == "verify"]
    if not v:
        return "실행 안 함"
    last = v[-1]
    m = re.search(r"(\d+) passed", str(last.get("output_tail", "")))
    state = "건너뜀(테스트 명령 없음)" if last.get("skipped") else ("통과" if last.get("passed") else "실패")
    return f"{state}" + (f" · {m.group(1)}개 통과" if m else "") + (f" · 명령: {last.get('command')}" if last.get("command") else "")


def _section(title: str, rows: list, lines: Optional[list] = None) -> dict:
    return {"title": title, "rows": [[a, b] for a, b in rows if b not in ("", None)], "lines": lines or []}


def _describe_run(project: Path, state_dir: str, run_id: str, marks: dict) -> dict:
    d = project / state_dir / "runs" / run_id
    events = _events(d / "events.jsonl")
    if not events:
        raise ManageError(404, "이 실행의 기록이 없습니다.")
    run = summarize_run(run_id, events)
    res = _read_json(d / "result.json")
    status = res.get("status", "")
    began, ended, secs = _span(events)
    patch = d / "result.patch"
    text = patch.read_text(encoding="utf-8", errors="replace") if patch.is_file() else ""
    files, (added, removed) = patch_files(text), patch_stats(text)
    mark = marks.get(f"run:{run_id}") or {}
    tokens = sum(st.tokens for st in run.providers.values())
    ais = [f"{name} · {st.calls}회 · {st.cli_sec:.0f}초 · 토큰 {st.tokens:,}" for name, st in run.providers.items()]
    reviews = [e for e in events if e.get("event") == "review_role"]
    steps = [l for l in run.timeline]
    attempts = []
    for a in run.attempt_details:
        v, r = a.get("verify") or {}, a.get("review") or {}
        attempts.append(f"시도 {a['attempt']}: 바뀐 파일 {len(a.get('changes') or [])}개 · "
                        + ("범위 위반 " + str(len(a['violations'])) + "건 · " if a.get("violations") else "")
                        + ("테스트 " + ("통과" if v.get("passed") else "실패") + " · " if v else "")
                        + ("검수 " + str(r.get("verdict")) if r else "검수 전"))
    warn = list(run.warnings)
    process = build_process(events)
    dirty = set(_dirty(project)) if (project / ".git").exists() or gitops.git(["rev-parse", "--git-dir"], project, check=False).returncode == 0 else set()
    overlap = sorted(set(files) & dirty) if mark.get("state") not in ("applied", "committed") else []
    applicable, reason = (None, "")
    if text and mark.get("state") not in ("applied", "committed", "discarded") and not (d / "applied.json").exists():
        applicable, reason = _check(project, patch)
    assessment = build_assessment(process, status, files, added, removed, overlap, applicable, reason, run.roles)
    return {"live": process["live"], "process": process, "assessment": assessment, "kind": "run", "id": run_id, "status": status, "status_ko": STATUS_KO.get(status, status or "진행 중"),
            "headline": f"{STATUS_KO.get(status, status or '진행 중')} — {short_goal(run.goal)[:90]}",
            "sections": {
                "who": _section("누가", [["역할 배정", " · ".join(f"{ROLE_KO.get(r, r)}={a}" for r, a in run.roles.items())]],
                                ais + [f"검수 결과 [{e.get('provider')}] {e.get('verdict')}" for e in reviews]),
                "what": _section("무엇을", [["목표", short_goal(run.goal)], ["결과", status and f"{STATUS_KO.get(status, status)} — {res.get('summary', '')}"[:300]],
                                            ["바뀐 파일", f"{len(files)}개 (+{added} −{removed})" if files else ""]], files[:30]),
                "when": _section("언제", [["시작", began], ["끝", ended], ["걸린 시간", f"{secs // 60}분 {secs % 60}초" if secs is not None else ""],
                                          ["적용", mark.get("at", "") if mark.get("state") in ("applied", "committed") else ""],
                                          ["커밋", mark.get("at", "") if mark.get("state") == "committed" else ""]]),
                "where": _section("어디서", [["프로젝트", str(project)], ["기록 폴더", str(d)], ["패치", str(patch) if text else ""],
                                             ["커밋", mark.get("sha", "")]]),
                "why": _section("왜", [["요청 내용", short_goal(run.goal)]], [run.goal[:1500]] if run.goal != short_goal(run.goal) else []),
                "how": _section("어떻게", [["시도 횟수", f"{run.attempts}번"], ["테스트", _tests_line(events)],
                                           ["토큰", f"{tokens:,}" if tokens else ""]], attempts + warn + steps[-12:]),
            }}


def _describe_batch(project: Path, state_dir: str, batch_id: str, marks: dict, key: str) -> dict:
    d = project / state_dir / "batches" / batch_id
    events = _events(d / "events.jsonl")
    b = _read_json(d / "batch.json")
    if not events and not b:
        raise ManageError(404, "이 배치의 기록이 없습니다.")
    start = next((e for e in events if e.get("event") == "batch_start"), {})
    began, ended, secs = _span(events)
    items = b.get("items") or start.get("items") or []
    mark = marks.get(key) or {}
    why_lines = []
    analysis = {}
    aid = start.get("analysis")
    if aid and re.match(r"^[0-9a-zA-Z-]+$", str(aid)):
        analysis = _read_json(project / state_dir / "analyses" / str(aid) / "analysis.json")
    clusters = {c.get("id"): c for c in analysis.get("clusters", [])}
    lines = []
    for n, it in enumerate(items, 1):
        c = clusters.get(it.get("cluster"), {})
        st = STATUS_KO.get(it.get("status", ""), it.get("status", ""))
        lines.append(f"{n}. [{st}] {it.get('title', '')} — {it.get('file', '')}" + (f" · 시도 {it['attempts']}번" if it.get("attempts") else "")
                     + (f" · 커밋 {it['commit'][:8]}" if it.get("commit") else ""))
        if c:
            why_lines.append(f"{n}. {it.get('title', '')}: {', '.join(c.get('ais', []))}가 지적 ({c.get('agreement')}명 동의), "
                             f"{'근거 확인됨' if c.get('verified') else '근거 미확인'}, 심각도 {c.get('severity')}")
    patch = d / "combined.patch"
    text = patch.read_text(encoding="utf-8", errors="replace") if patch.is_file() else ""
    files, (added, removed) = patch_files(text), patch_stats(text)
    ok = sum(1 for i in items if i.get("status") == "PASSED")
    status = b.get("status", "RUNNING")
    checks = [{"label": f"항목 {n}", "status": "ok" if i.get("status") == "PASSED" else "warn",
               "text": f"{i.get('title', '')} — {STATUS_KO.get(i.get('status', ''), i.get('status', ''))}"} for n, i in enumerate(items, 1)]
    dirty = set(_dirty(project))
    overlap = sorted(set(files) & dirty) if mark.get("state") not in ("applied", "committed") else []
    if overlap:
        checks.append({"label": "작업 폴더 충돌", "status": "warn", "text": "이미 바뀐 파일과 겹칩니다: " + ", ".join(overlap[:5])})
    if text and mark.get("state") not in ("applied", "committed", "discarded"):
        okp, why = _check(project, patch)
        if not okp:
            checks.append({"label": "적용 가능 여부", "status": "fail", "text": "지금은 적용할 수 없습니다: " + why})
    if len(files) > 8 or added + removed > 400:
        checks.append({"label": "변경 규모", "status": "warn", "text": f"파일 {len(files)}개 · +{added} −{removed}줄 — 큰 변경입니다"})
    rec = "block" if any(c["status"] == "fail" for c in checks) or not ok else "caution" if any(c["status"] == "warn" for c in checks) else "recommend"
    assessment = {"recommendation": rec, "checks": checks,
                  "headline": {"block": "전체 적용을 권하지 않습니다 (항목별로 판단하세요)", "caution": "확인할 점이 있습니다 — 항목별로 살펴보세요",
                               "recommend": "모든 항목이 점검을 통과했습니다"}[rec]}
    return {"live": not b, "process": {"prelude": [], "attempts": [], "live": not b}, "assessment": assessment,
            "kind": "batch", "id": batch_id, "status": status, "status_ko": STATUS_KO.get(status, status),
            "headline": f"{STATUS_KO.get(status, status)} — {len(items)}건 중 {ok}건 수정",
            "sections": {
                "who": _section("누가", [["수정 주체", "Solomon (AI 구현 + 교차 검수)"], ["커밋 작성자", "Solomon <solomon@localhost>" if b.get("branch") else ""]]),
                "what": _section("무엇을", [["결과", b.get("summary", "")], ["바뀐 파일", f"{len(files)}개 (+{added} −{removed})" if files else ""]], lines),
                "when": _section("언제", [["시작", began], ["끝", ended], ["걸린 시간", f"{secs // 60}분" if secs is not None else ""],
                                          ["적용", mark.get("at", "") if mark.get("state") in ("applied", "committed") else ""]]),
                "where": _section("어디서", [["프로젝트", str(project)], ["수정 브랜치", b.get("branch", "") or start.get("branch", "")],
                                             ["기록 폴더", str(d)], ["합친 패치", str(patch) if text else ""]], files[:30]),
                "why": _section("왜", [["근거 분석", str(aid or "")], ["선정 기준", "심각도·근거 확인·AI 동의 기준을 넘은 항목"]], why_lines),
                "how": _section("어떻게", [["방식", "항목마다 계획→구현→범위 검사→테스트→교차 검수→테스트 재확인, 앞 수정 위에 이어서 진행"],
                                           ["시간 예산", f"{start.get('budget_minutes')}분" if start.get("budget_minutes") else ""]],
                                [f"{str(e.get('ts', ''))[11:]}  항목 {e.get('n')} {STATUS_KO.get(e.get('status'), e.get('status'))}"
                                 for e in events if e.get("event") == "item_end"]),
            }}


def _describe_analysis(project: Path, state_dir: str, analysis_id: str) -> dict:
    from . import analysis as analysis_mod
    view = analysis_mod.load_analysis(project, state_dir, analysis_id)
    if not view:
        raise ManageError(404, "이 분석의 기록이 없습니다.")
    d = project / state_dir / "analyses" / analysis_id
    began, ended, secs = _span(_events(d / "events.jsonl"))
    clusters = view.get("clusters", [])
    return {"kind": "analysis", "id": analysis_id, "status": view.get("status", ""), "status_ko": view.get("status", ""),
            "headline": f"{view.get('status', '')} — 발견 {len(clusters)}건",
            "sections": {
                "who": _section("누가", [["분석한 AI", ", ".join((view.get("ais") or {}).keys())]]),
                "what": _section("무엇을", [["요약", view.get("summary", "")], ["발견", f"{len(clusters)}건 (근거 확인 {sum(1 for c in clusters if c.get('verified'))}건)"]],
                                 [f"[{c.get('severity')}] {c['members'][0]['title']} — {c.get('file')}:{c.get('line_start')} ({c.get('agreement')}명 동의)"
                                  for c in clusters[:25]]),
                "when": _section("언제", [["시작", began], ["끝", ended], ["걸린 시간", f"{secs // 60}분 {secs % 60}초" if secs is not None else ""]]),
                "where": _section("어디서", [["프로젝트", str(project)], ["보고서", view.get("report_path", "")],
                                             ["분석한 파일", str((view.get("coverage") or {}).get("included", ""))]]),
                "why": _section("왜", [["분석 요청", view.get("focus") or "전반 분석"]]),
                "how": _section("어떻게", [["방식", "AI가 각자 독립적으로 읽기 전용 분석 → 인용 문구를 원본에서 기계적으로 검증 → 비슷한 지적끼리 묶기"]],
                                list(view.get("timeline", []))[-12:]),
            }}


def describe(project: Path, state_dir: str, key: str) -> dict:
    """Everything about one id, in the 5W1H order: who, what, when, where, why, how."""
    project = Path(project)
    marks = _load_marks(project, state_dir)
    if key.startswith("analysis:"):
        aid = key.split(":", 1)[1]
        if not re.match(r"^[0-9a-zA-Z-]+$", aid):
            raise ManageError(400, "항목 식별자가 올바르지 않습니다.")
        return _describe_analysis(project, state_dir, aid)
    kind, ident, sha = parse_key(key)
    if kind == "run":
        return _describe_run(project, state_dir, ident, marks)
    if kind == "batch":
        return _describe_batch(project, state_dir, ident, marks, key)
    batch = _read_json(project / state_dir / "batches" / ident / "batch.json")
    it = next((i for i in batch.get("items", []) if (i.get("commit") or "").startswith(sha)), None)
    if not it or not it.get("run_id"):
        raise ManageError(404, "이 항목의 실행 기록이 없습니다.")
    out = _describe_run(project, state_dir, it["run_id"], marks)
    out["sections"]["why"]["rows"].insert(0, ["밤샘 수정 항목", f"{it.get('title', '')} ({it.get('file', '')})"])
    return out


def list_history(project: Path, state_dir: str, limit: int = 40) -> list[dict]:
    """Every id Solomon has produced for this project, newest first, so any of them can be opened as a 5W1H."""
    root = Path(project) / state_dir
    rows = []
    for sub, kind, pat in (("runs", "run", RUN_ID), ("batches", "batch", BATCH_ID), ("analyses", "analysis", re.compile(r"^[0-9a-zA-Z-]+$"))):
        if not (root / sub).is_dir():
            continue
        for d in (root / sub).iterdir():
            if not pat.match(d.name):
                continue
            title, status = "", ""
            if kind == "run":
                res = _read_json(d / "result.json")
                status, title = res.get("status", "진행 중/중단"), short_goal(_goal(d))
            elif kind == "batch":
                b = _read_json(d / "batch.json")
                status, title = b.get("status", "진행 중/중단"), b.get("summary", "")
            else:
                a = _read_json(d / "analysis.json")
                status, title = a.get("status", "진행 중/중단"), (a.get("focus") or "전반 분석")
            rows.append({"key": f"{kind}:{d.name}", "kind": kind, "id": d.name, "when": _when(d.name), "title": title[:100],
                         "status": status, "status_ko": STATUS_KO.get(status, status)})
    rows.sort(key=lambda r: r["when"], reverse=True)
    return rows[:limit]


# ------------------------------------------------------------------ step-by-step process and apply assessment
def _tail(text: object, n: int = 500) -> str:
    return str(text or "").strip()[-n:]


def build_process(events: list[dict]) -> dict:
    """The run as a person would narrate it: plan, then per attempt build / changes / scope / tests / reviews / re-check."""
    prelude: list[dict] = []
    attempts: list[dict] = []
    cur: Optional[dict] = None
    live = True
    pending_scope: Optional[dict] = None
    wrote: dict[str, bool] = {}

    def step(target: list, name: str, status: str, text: str = "", lines: Optional[list] = None, ts: str = "") -> dict:
        d = {"name": name, "status": status, "text": text, "lines": lines or [], "at": str(ts)[11:19]}
        target.append(d)
        return d

    for e in events:
        k, ts = e.get("event"), e.get("ts", "")
        tgt = cur["steps"] if cur else prelude
        if k == "call_start":
            wrote[str(e.get("role"))] = bool(e.get("write"))
        elif k == "call_end":
            role = str(e.get("role"))
            sec = f"{float(e.get('duration_sec') or 0):.0f}초"
            if role == "planner":
                step(prelude, "계획", "ok", f"{e.get('provider')}가 작업 지시서를 만들었습니다 ({sec})", ts=ts)
            elif role.startswith("builder") and wrote.get(role) and cur is not None:
                step(cur["steps"], "구현", "ok", f"{e.get('provider')}가 코드를 작성했습니다 ({sec})", ts=ts)
            elif role.startswith("builder") and cur is not None:
                step(cur["steps"], "구현 자문", "info", f"{e.get('provider')}가 구현 조언을 했습니다 ({sec})", ts=ts)
        elif k == "plan_review":
            v = str(e.get("verdict"))
            step(prelude, f"계획 검토 · {e.get('provider')}", "ok" if v == "OK" else "warn" if v in ("CHANGE",) else "info",
                 {"OK": "이상 없음", "CHANGE": "보완 제안", "UNPARSEABLE": "응답을 해석하지 못해 무시"}.get(v, v),
                 [f"우려: {c}" for c in e.get("concerns", [])] + [f"완료 기준 추가: {c}" for c in e.get("added", [])], ts)
        elif k == "helper_skipped":
            step(tgt, "보조 AI 건너뜀", "warn", f"{e.get('provider')}: {_tail(e.get('reason'), 120)}", ts=ts)
        elif k == "builder_pick":
            att = next((a for a in attempts if a["n"] == e.get("attempt")), None)
            if att is not None:
                att["builder"] = e.get("provider")
        elif k == "review_skipped_self":
            step(tgt, "검수 건너뜀", "info", f"{e.get('provider')}가 이번 시도의 구현자라 스스로 검수하지 않습니다", ts=ts)
        elif k == "call_error":
            step(tgt, "AI 호출 실패", "fail", f"{e.get('role')} · {e.get('provider')}: {_tail(e.get('error'), 160)}", ts=ts)
        elif k == "quota":
            step(tgt, "구독 한도", "warn", f"{e.get('provider')}의 사용 한도에 도달했습니다", ts=ts)
        elif k == "order":
            o = e.get("order") or {}
            lines = [f"수정 가능: {', '.join(o.get('allowed_paths', []))}"]
            if o.get("protected_paths"):
                lines.append(f"보호 경로: {', '.join(o['protected_paths'])}")
            lines += [f"완료 기준: {c}" for c in o.get("acceptance_criteria", [])]
            lines.append(f"한도: 파일 {o.get('max_files_changed')}개 · {o.get('max_diff_lines')}줄")
            step(prelude, "작업 지시서", "info", "무엇을 어디까지 고칠지 정했습니다", lines, ts)
        elif k == "baseline":
            step(prelude, "시작 전 점검", "ok" if e.get("passed") else "fail",
                 "수정 전 코드의 테스트 " + ("통과" if e.get("passed") else "실패 → AI를 부르지 않고 멈춤"),
                 [_tail(e.get("output_tail"), 300)] if not e.get("passed") else [], ts)
        elif k == "attempt_start":
            cur = {"n": e.get("attempt"), "steps": [], "result": "진행 중", "builder": ""}
            attempts.append(cur)
            pending_scope = None
        elif k == "changes" and cur is not None:
            files = e.get("files") or []
            step(cur["steps"], "바뀐 파일", "info", f"{e.get('count', len(files))}개",
                 [f"{f.get('status')} {f.get('path')} (+{f.get('added')} −{f.get('removed')})" for f in files[:25]], ts)
            pending_scope = step(cur["steps"], "범위 검사", "ok", "지시서 범위 안에서 수정했습니다", ts=ts)
        elif k == "scope_violation" and cur is not None:
            if pending_scope:
                pending_scope.update(status="fail", text="범위를 벗어난 수정을 버렸습니다", lines=[str(v) for v in e.get("violations", [])])
            cur["result"] = "범위 위반 → 되돌리고 재시도"
        elif k == "verify" and cur is not None:
            st = "warn" if e.get("skipped") else "ok" if e.get("passed") else "fail"
            m = re.search(r"(\d+) passed", str(e.get("output_tail", "")))
            txt = "테스트 명령이 없어 건너뜀 (검증 안 됨)" if e.get("skipped") else ("통과" if e.get("passed") else f"실패 (종료 코드 {e.get('exit_code')})")
            step(cur["steps"], "테스트", st, txt + (f" · {m.group(1)}개 통과" if m else ""),
                 ([f"명령: {e.get('command')}"] if e.get("command") else []) + ([_tail(e.get("output_tail"), 600)] if st == "fail" else []), ts)
        elif k == "review_role" and cur is not None:
            v = str(e.get("verdict"))
            step(cur["steps"], f"검수 · {e.get('provider')}", "fail" if e.get("error") else "ok" if v == "PASS" else "fail",
                 "판정 없음(오류)" if e.get("error") else v, [str(i) for i in (e.get("issues") or [])][:10], ts)
        elif k == "review_retry" and cur is not None:
            step(cur["steps"], "검수 재요청", "warn", f"{e.get('role')}가 판정을 못 내 같은 코드로 다시 물었습니다", [_tail(e.get("reason"), 160)], ts)
        elif k == "double_check" and cur is not None:
            step(cur["steps"], "테스트 재확인", "ok" if e.get("passed") else "fail",
                 "한 번 더 실행해도 통과" if e.get("passed") else "다시 실행하니 실패 (불안정한 테스트)", ts=ts)
        elif k == "stagnation":
            step(tgt, "접근 변경", "warn", "같은 실패가 반복돼 코드를 처음 상태로 되돌리고 다른 방식으로 시도합니다", ts=ts)
        elif k == "review" and cur is not None:
            cur["result"] = {"PASS": "검수 통과", "FAIL": "검수 불합격 → 지적을 반영해 재시도", "ERROR": "검수 판정 불가"}.get(str(e.get("verdict")), "")
        elif k == "run_end":
            live = False
            end = e
            if cur is not None and e.get("status") == "PASSED":
                cur["result"] = "통과 → 결과 확정"
    return {"prelude": prelude, "attempts": attempts, "live": live}


def build_assessment(process: dict, status: str, files: list, added: int, removed: int, overlap: list, applicable: Optional[bool],
                     reason: str, roles: Optional[dict] = None) -> dict:
    """A plain checklist behind 'should I apply this?'. Facts only: every line is something the pipeline measured."""
    checks: list[dict] = []

    def add(label: str, st: str, text: str) -> None:
        checks.append({"label": label, "status": st, "text": text})

    if status != "PASSED":
        add("실행 결과", "fail" if status else "warn", "아직 끝나지 않았거나 통과하지 못한 실행입니다" if not status else f"결과가 통과가 아닙니다 ({STATUS_KO.get(status, status)})")
        return {"recommendation": "block", "headline": "적용할 수 없는 결과입니다", "checks": checks}
    last = process["attempts"][-1]["steps"] if process["attempts"] else []
    by = lambda name: [s for s in last if s["name"].startswith(name)]
    sc = by("범위 검사")
    add("범위 검사", "ok" if sc and sc[-1]["status"] == "ok" else "warn", "지시서 범위 안에서만 수정했고 보호 경로를 건드리지 않았습니다" if sc and sc[-1]["status"] == "ok" else "기록 없음")
    t = by("테스트")
    t = [x for x in t if x["name"] == "테스트"]
    if t:
        add("테스트", t[-1]["status"], t[-1]["text"])
    else:
        add("테스트", "warn", "기록 없음")
    rc = by("테스트 재확인")
    if rc:
        add("테스트 재확인", rc[-1]["status"], rc[-1]["text"])
    revs = [x for x in last if x["name"].startswith("검수 ·")]
    if revs:
        for r in revs:
            add(r["name"].replace("검수 · ", "교차 검수 (") + ")", r["status"], r["text"])
    else:
        add("교차 검수", "warn", "기록 없음")
    if roles and roles.get("builder"):
        final_builder = (process["attempts"][-1].get("builder") if process["attempts"] else "") or roles["builder"]
        judged = [x["name"].replace("검수 · ", "") for x in last if x["name"].startswith("검수 ·")]
        same = [j for j in judged if j == final_builder]
        add("작성자와 검수자", "warn" if same else "ok",
            "작성한 AI가 직접 검수했습니다 (독립성 낮음)" if same else f"구현 {final_builder} ≠ 검수 " + ", ".join(judged or ["?"]) + " (서로 다른 AI)")
    size = f"파일 {len(files)}개 · +{added} −{removed}줄"
    add("변경 규모", "warn" if len(files) > 8 or added + removed > 400 else "ok", size + (" — 큰 변경이니 내용을 꼼꼼히 보세요" if len(files) > 8 or added + removed > 400 else ""))
    if overlap:
        add("작업 폴더 충돌", "warn", "지금 작업 폴더에서 이미 바뀐 파일과 겹칩니다: " + ", ".join(overlap[:5]))
    if applicable is False:
        add("적용 가능 여부", "fail", "지금은 적용할 수 없습니다: " + (reason or "충돌"))
    bad = [c for c in checks if c["status"] == "fail"]
    warn = [c for c in checks if c["status"] == "warn"]
    if bad:
        return {"recommendation": "block", "headline": "적용을 권하지 않습니다", "checks": checks}
    if warn:
        return {"recommendation": "caution", "headline": "확인할 점이 있습니다", "checks": checks}
    return {"recommendation": "recommend", "headline": "모든 점검을 통과했습니다 — 변경 내용만 훑어보고 적용하세요", "checks": checks}
