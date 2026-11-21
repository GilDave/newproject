"""Question -> document: several AIs answer a free-form question independently, one of them merges the drafts into a single report.

    "유튜브 자막으로 ena-bot 모델을 키우는 방법 보고서와 구현 방안"  ->  a Markdown document with a one-line conclusion, the method,
    an implementation plan, risks, a to-do list, and a table of where the AIs disagreed.

Read-only: nothing in the project is changed. Only signed-in subscription CLIs (and the local model) are used. The project folder, when
asked for, contributes the same safe context the video analysis uses (file names and the start of the README, no secrets).
"""

from __future__ import annotations

import json
import re
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from . import gitops
from .providers.base import Provider, ProviderError, QuotaExhausted
from .runner import kill_active
from .settings import Settings
from .tokens import estimate_tokens
from .video import project_context

MAX_QUESTION = 6000
REPORT_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
SYNTH_ORDER = ("claude", "codex", "antigravity", "ollama")  # who merges the drafts: the first of these that wrote one


class ReportError(ValueError):
    pass


def draft_prompt(question: str, context: str = "") -> str:
    return (
        "You are an expert analyst writing a report for a developer who runs a crypto trading bot project and several AI helpers.\n"
        "Answer the QUESTION below as a complete REPORT written in KOREAN, in Markdown. Use exactly this structure:\n"
        "# <short title>\n"
        "## 한 줄 결론\n"
        "## 배경과 목적\n"
        "## 핵심 내용  (use lists and tables; explain WHY, not only what)\n"
        "## 구현 방안  (numbered steps; name concrete files, functions, data and commands when you can; how to verify each step; what to do first)\n"
        "## 위험과 한계  (what can go wrong, cost, legal/terms-of-service limits, how to keep it safe)\n"
        "## 다음에 할 일  (a checklist of 5-10 small actions in order)\n"
        "## 근거와 불확실한 점  (mark every claim you are not sure about as 추측; never invent sources, numbers or quotes)\n"
        "Rules: be specific and practical, no filler. Do not claim you ran or tested anything. Output ONLY the Markdown report.\n"
        + (f"\nPROJECT CONTEXT (the user's project folder; use it to make the plan concrete):\n{context}\n" if context else "")
        + f"\nQUESTION:\n{question}\n"
    )


def merge_prompt(question: str, drafts: dict[str, str]) -> str:
    body = "\n\n".join(f"===== DRAFT {i + 1} (written independently by {name}) =====\n{text}" for i, (name, text) in enumerate(drafts.items()))
    return (
        "Several AIs answered the same QUESTION independently. Merge their drafts into ONE final report in KOREAN Markdown.\n"
        "Keep the structure: # title, ## 한 줄 결론, ## 배경과 목적, ## 핵심 내용, ## 구현 방안, ## 위험과 한계, ## 다음에 할 일, ## 근거와 불확실한 점, "
        "and add a last section ## AI 간 의견 차이 with a table: | 주제 | AI별 의견 | 판단 |.\n"
        "Rules:\n"
        "- What several drafts agree on is stated as settled. Where they disagree, put it in the table and say which view you would follow and why.\n"
        "- A claim only ONE draft makes is marked (단독 주장 · 검증 필요).\n"
        "- Do not add facts, numbers, sources or quotes that are in none of the drafts. Remove anything that looks invented.\n"
        "- Keep the best concrete implementation steps; merge duplicates; keep the checklist short and ordered.\n"
        "- Output ONLY the final Markdown report.\n"
        f"\nQUESTION:\n{question}\n\n{body}\n"
    )


def _clean(text: str) -> str:
    """Models sometimes wrap the whole answer in a code fence or add a chatty first line."""
    t = (text or "").strip()
    m = re.match(r"^```(?:markdown|md)?\s*\n(.*)\n```\s*$", t, re.S)
    if m:
        t = m.group(1).strip()
    i = t.find("\n# ")
    if i > 0 and not t.startswith("#") and i < 400:  # a short preamble before the title
        t = t[i + 1:]
    return t


def _title(md: str, fallback: str) -> str:
    m = re.search(r"^#\s+(.+)$", md, re.M)
    return (m.group(1).strip() if m else fallback)[:120]


class _Log:
    def __init__(self, path: Path):
        self.path = path

    def event(self, kind: str, **fields) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": kind, **fields}, ensure_ascii=False, default=str) + "\n")


def run_report(settings: Settings, project: Path, question: str, ais: Optional[list[str]] = None, *, use_project_context: bool = False,
               providers: Optional[dict[str, Provider]] = None) -> dict:
    from .providers import build_providers, default_ais

    question = (question or "").strip()
    if not question:
        raise ReportError("질문을 입력하세요.")
    if len(question) > MAX_QUESTION:
        raise ReportError(f"질문이 너무 깁니다 ({MAX_QUESTION}자 이하).")
    project = Path(project).resolve()
    providers = providers if providers is not None else build_providers(settings)
    chosen = ais or default_ais(providers)
    unknown = [a for a in chosen if a not in providers]
    if unknown:
        raise ValueError(f"unknown AI: {', '.join(unknown)} (known: {', '.join(providers)})")
    if not chosen:
        raise ValueError("no AI CLI is installed")

    rid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    rdir = project / settings.state_dir / "reports" / rid
    rdir.mkdir(parents=True)
    if gitops.git(["rev-parse", "--is-inside-work-tree"], project, check=False).returncode == 0:
        gitops.exclude_from_git(project, settings.state_dir)
    log = _Log(rdir / "events.jsonl")
    result: dict = {"report_id": rid, "status": "FAILED", "question": question, "project": str(project), "ais": {}, "synthesizer": "", "title": "",
                    "created": time.strftime("%Y-%m-%d %H:%M:%S"), "context": bool(use_project_context)}
    log.event("report_start", project=str(project), question=question, ais=chosen, context=bool(use_project_context))

    ctx = ""
    if use_project_context:
        try:
            ctx = project_context(project)
        except Exception as exc:  # the report is still worth writing without it
            log.event("context_failed", error=str(exc)[:200])
    timeout = float(settings.timeouts.get("analysis", 900.0))
    sandbox = tempfile.TemporaryDirectory(prefix="solomon-report-")
    gitops.git(["init", "-q"], sandbox.name)

    def ask(name: str) -> dict:
        prompt = draft_prompt(question, ctx)
        log.event("ai_start", provider=name, role="draft", prompt_chars=len(prompt))
        started = time.time()
        try:
            res = providers[name].run(prompt, cwd=sandbox.name, write=False, timeout=timeout, no_output_timeout=None)
        except QuotaExhausted as exc:
            return {"ok": False, "state": "quota", "error": f"구독 한도 소진: {exc}"[:300]}
        except ProviderError as exc:
            return {"ok": False, "state": "failed", "error": str(exc)[:300]}
        text = _clean(res.text)
        if len(text) < 200 or "#" not in text:
            return {"ok": False, "state": "failed", "error": "보고서 형식의 답이 오지 않았습니다 (너무 짧거나 제목이 없음)"}
        tokens = res.tokens or {"in": estimate_tokens(prompt), "out": estimate_tokens(res.text), "estimated": True}
        return {"ok": True, "state": "done", "text": text, "duration_sec": round(time.time() - started, 1), "tokens": tokens}

    outcomes: dict[str, dict] = {}
    executor = ThreadPoolExecutor(max_workers=len(chosen))
    futures = {executor.submit(ask, n): n for n in chosen}
    try:
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                out = fut.result()
            except Exception as exc:
                out = {"ok": False, "state": "failed", "error": f"내부 오류: {exc}"[:300]}
            outcomes[name] = out
            if out["ok"]:
                (rdir / f"draft-{name}.md").write_text(out["text"], encoding="utf-8")
            log.event("ai_end", provider=name, role="draft", ok=out["ok"], state=out.get("state"), duration_sec=out.get("duration_sec"),
                      tokens=out.get("tokens"), error=out.get("error"), chars=len(out.get("text", "")))
    except KeyboardInterrupt:
        kill_active()
        executor.shutdown(wait=False, cancel_futures=True)
        sandbox.cleanup()
        result.update(status="STOPPED", summary="사용자가 중지했습니다")
        for n in chosen:
            done = outcomes.get(n)
            result["ais"][n] = {"ok": bool(done and done["ok"]), "state": "stopped" if not done else done.get("state"), "error": "" if done and done["ok"] else "중지됨"}
        log.event("report_end", status="STOPPED", summary=result["summary"])
        _save(result, rdir)
        return result
    executor.shutdown(wait=True)

    drafts = {n: o["text"] for n, o in outcomes.items() if o["ok"]}
    for n in chosen:
        o = outcomes.get(n, {"ok": False, "state": "failed", "error": "응답 없음"})
        result["ais"][n] = {"ok": o["ok"], "state": o.get("state"), "error": o.get("error", ""), "duration_sec": o.get("duration_sec"),
                            "tokens": o.get("tokens"), "chars": len(o.get("text", ""))}
    if not drafts:
        result["summary"] = "어떤 AI도 보고서를 쓰지 못했습니다: " + "; ".join(f"{n}: {i['error']}" for n, i in result["ais"].items() if i["error"])[:400]
        log.event("report_end", status="FAILED", summary=result["summary"])
        sandbox.cleanup()
        _save(result, rdir)
        return result

    final = ""
    notes: list[str] = []
    if len(drafts) >= 2:
        order = [n for n in SYNTH_ORDER if n in drafts] + [n for n in drafts if n not in SYNTH_ORDER]
        for who in order:
            log.event("merge_start", provider=who, drafts=len(drafts))
            started = time.time()
            try:
                res = providers[who].run(merge_prompt(question, drafts), cwd=sandbox.name, write=False, timeout=timeout, no_output_timeout=None)
            except (QuotaExhausted, ProviderError) as exc:
                log.event("merge_end", provider=who, ok=False, error=str(exc)[:200])
                continue
            text = _clean(res.text)
            ok = len(text) >= 200 and "#" in text
            log.event("merge_end", provider=who, ok=ok, duration_sec=round(time.time() - started, 1), tokens=res.tokens, chars=len(text))
            if ok:
                final, result["synthesizer"] = text, who
                break
        if not final:
            notes.append("통합을 맡을 AI가 응답하지 못해, 가장 긴 초안을 그대로 보여 줍니다")
    if not final:
        who = max(drafts, key=lambda n: len(drafts[n]))
        final, result["synthesizer"] = drafts[who], who
        if len(drafts) == 1:
            notes.append(f"{who} 한 개만 응답해 교차 검증이 되지 않았습니다 — 다른 AI의 의견이 없는 초안입니다")
    if notes:
        final += "\n\n> ⚠ " + " / ".join(notes) + "\n"
    sandbox.cleanup()
    (rdir / "report.md").write_text(final, encoding="utf-8")
    result.update(status="DONE", title=_title(final, question[:60]), report_path=str(rdir / "report.md"),
                  summary=f"AI {len(drafts)}/{len(chosen)}개의 초안을 {result['synthesizer'] or '-'}가 통합했습니다" if len(drafts) > 1 else "초안 1개 (교차 검증 안 됨)")
    log.event("report_end", status="DONE", summary=result["summary"])
    _save(result, rdir)
    return result


def delete_report(project: Path, state_dir: str, rid: str) -> None:
    """Remove one report folder (the id is validated: nothing outside .solomon/reports can be reached)."""
    import shutil

    if not REPORT_ID.match(rid or ""):
        raise ReportError("보고서 번호가 올바르지 않습니다.")
    root = (Path(project) / state_dir / "reports").resolve()
    target = (root / rid).resolve()
    if target.parent != root or not target.is_dir():
        raise ReportError("보고서를 찾을 수 없습니다.")
    shutil.rmtree(target)


def _safe_name(text: str) -> str:
    t = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", " ", text or "").strip(" .")
    return re.sub(r"\s+", " ", t)[:60] or "보고서"


def export_report(project: Path, state_dir: str, rid: str, folder: str) -> Path:
    """Copy the final report as a .md file into a folder the person chose (inside the home folder). Never overwrites."""
    from . import folders

    view = load_report(project, state_dir, rid)
    if view is None or not view.get("markdown"):
        raise ReportError("내보낼 보고서가 없습니다 (아직 작성 중이거나 실패한 보고서입니다).")
    dest = Path(folder or "").expanduser()
    if not dest.is_absolute() or not folders.within_home(dest) or not dest.is_dir():
        raise ReportError("저장할 폴더는 홈 폴더 안에 있는 기존 폴더여야 합니다.")
    base = f"{_safe_name(view.get('title') or view.get('question', ''))}-{rid}"
    target = dest.resolve() / f"{base}.md"
    n = 2
    while target.exists():
        target = dest.resolve() / f"{base}-{n}.md"
        n += 1
    target.write_text(view["markdown"], encoding="utf-8")
    return target


def _save(result: dict, rdir: Path) -> None:
    (rdir / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def _events(path: Path) -> list[dict]:
    out = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def _progress(events: list[dict]) -> dict:
    """What the screen needs while a report is being written: who has answered, who is merging."""
    view: dict = {"ais": {}, "stage": "시작", "finished": False, "status": "진행 중", "summary": "", "question": "", "started": None}
    for ev in events:
        k = ev.get("event")
        if k == "report_start":
            view["question"] = str(ev.get("question", ""))
            view["started"] = ev.get("ts")
            view["ais"] = {a: {"state": "waiting"} for a in ev.get("ais", [])}
            view["stage"] = "AI들이 초안을 쓰는 중"
        elif k == "ai_start":
            view["ais"].setdefault(str(ev.get("provider")), {})["state"] = "running"
        elif k == "ai_end":
            ok = bool(ev.get("ok"))
            view["ais"].setdefault(str(ev.get("provider")), {}).update(state="done" if ok else str(ev.get("state") or "failed"), error=str(ev.get("error") or ""),
                                                                      sec=ev.get("duration_sec"))
        elif k == "merge_start":
            view["stage"] = f"{ev.get('provider')}가 초안 {ev.get('drafts')}개를 하나로 통합하는 중"
        elif k == "report_end":
            view.update(finished=True, status=str(ev.get("status", "")), summary=str(ev.get("summary", "")), stage="완료")
            view["ended"] = ev.get("ts")
    return view


def list_reports(project: Path, state_dir: str, limit: int = 40) -> list[dict]:
    root = Path(project) / state_dir / "reports"
    if not root.is_dir():
        return []
    rows = []
    for d in sorted(root.iterdir(), reverse=True)[:limit]:
        if not REPORT_ID.match(d.name):
            continue
        view = _progress(_events(d / "events.jsonl"))
        title = ""
        try:
            title = json.loads((d / "report.json").read_text(encoding="utf-8")).get("title", "")
        except (OSError, ValueError):
            pass
        rows.append({"report_id": d.name, "title": title or view["question"][:60], "question": view["question"][:200], "status": view["status"],
                     "finished": view["finished"], "created": view["started"], "ais": {n: a.get("state") for n, a in view["ais"].items()}})
    return rows


def load_report(project: Path, state_dir: str, rid: str) -> Optional[dict]:
    if not REPORT_ID.match(rid or ""):
        raise ReportError("보고서 번호가 올바르지 않습니다.")
    rdir = Path(project) / state_dir / "reports" / rid
    if not rdir.is_dir():
        return None
    view = _progress(_events(rdir / "events.jsonl"))
    meta: dict = {}
    try:
        meta = json.loads((rdir / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    md = ""
    try:
        md = (rdir / "report.md").read_text(encoding="utf-8")
    except OSError:
        pass
    drafts = {}
    for f in sorted(rdir.glob("draft-*.md")):
        drafts[f.stem[len("draft-"):]] = f.read_text(encoding="utf-8", errors="replace")
    view.update(report_id=rid, report_dir=str(rdir), markdown=md, drafts=drafts, title=meta.get("title", ""), synthesizer=meta.get("synthesizer", ""),
                project=meta.get("project", str(project)), context=meta.get("context", False), created=meta.get("created", view["started"]))
    view["interrupted"] = bool(not view["finished"] and not meta)
    return view


# ---------------------------------------------------------------- report -> small improvement tasks (the pipeline from a plan to the builders)
MAX_TASKS = 10
MAX_TASK_GOAL = 3000
_NEEDS_PERSON = re.compile(
    r"사용자[^.\n]{0,14}(검수|승인|확인|동의|리뷰)|사전\s*(검수|승인)|출처\s*링크|조회일|웹\s*(검색|서칭)|서칭|브라우저|렌더링\s*(확인|검증)|스크린샷|git\s*(diff|status|log)", re.I)


def split_prompt(question: str, report_md: str, context: str = "") -> str:
    return (
        "You are a technical lead. Below is a REPORT (a plan) written for the QUESTION. Split its implementation plan into SMALL work tasks that an "
        "automated pipeline can build one at a time. Each task is given to a builder AI that works OFFLINE in a copy of the project, writes code and tests, "
        "and is checked by the project's tests and by reviewers. It cannot browse the web, use a browser, run git, or ask anyone.\n"
        "Reply with ONLY one JSON object:\n"
        '{"tasks": [{"title": string, "goal": string, "size": "S"|"M", "files": string[], "depends_on": integer[], "verify": string}], "human_steps": string[]}\n'
        "Rules for the tasks (write every string in Korean):\n"
        f"- At most {MAX_TASKS} tasks, in the order they should be built. Numbering starts at 1; depends_on lists the numbers of tasks that must be applied first.\n"
        "- ONE feature slice per task, with its own tests: at most about 5 files and 400 changed lines. The first task should be the smallest useful, "
        "independent piece (pure functions and tests before anything that touches the screen or the network).\n"
        "- goal: a complete, self-contained instruction for the builder (what to build, function/file names, thresholds, inputs and outputs, edge cases, "
        "which tests to write). Copy the exact details it needs from the report. Do NOT ask for web research, sources, links, browser or screenshot checks, "
        "git output, or approval before coding.\n"
        "- files: the project files or folders the task will likely touch (from the project file list when given; new files are fine).\n"
        "- verify: how the result is checked by tests (one sentence).\n"
        "- human_steps: what only the PERSON can do (look at a chart, confirm a data source, register an API key ...). Not tasks.\n"
        + (f"\nPROJECT CONTEXT (file names and README start):\n{context}\n" if context else "")
        + f"\nQUESTION:\n{question}\n\nREPORT:\n{report_md[:30000]}\n"
    )


def _validate_tasks(data: dict) -> tuple[list[dict], list[str]]:
    raw = data.get("tasks")
    if not isinstance(raw, list) or not raw:
        raise ReportError("작업 목록이 비어 있습니다")
    tasks: list[dict] = []
    new_id: dict[int, int] = {}  # the position in the AI's list -> the number after dropping unusable entries
    human = [str(x).strip()[:300] for x in (data.get("human_steps") or []) if str(x).strip()][:10]
    for i, t in enumerate(raw[:MAX_TASKS], 1):
        if not isinstance(t, dict):
            continue
        title, goal = str(t.get("title", "")).strip()[:120], str(t.get("goal", "")).strip()[:MAX_TASK_GOAL]
        if not title:
            continue
        keep, moved = [], []
        for sentence in re.split(r"(?<=[.。!?])\s+|\n+", goal):  # what only a person can do leaves the builder's instruction
            if sentence.strip():
                (moved if _NEEDS_PERSON.search(sentence) else keep).append(sentence.strip())
        for m in moved:
            human.append(f"단계 {i} ({title}): {m[:200]}")
        goal = "\n".join(keep)
        if len(goal) < 20:
            continue
        files = [str(f).strip()[:200] for f in (t.get("files") or []) if str(f).strip() and ".." not in str(f) and not str(f).startswith("/")][:8]
        deps = sorted({new_id[int(d)] for d in (t.get("depends_on") or []) if isinstance(d, (int, float)) and not isinstance(d, bool) and int(d) in new_id})
        new_id[i] = len(tasks) + 1
        tasks.append({"id": len(tasks) + 1, "title": title, "goal": goal, "size": "M" if str(t.get("size", "S")).upper() == "M" else "S", "files": files,
                      "depends_on": deps, "verify": str(t.get("verify", "")).strip()[:300]})
    if not tasks:
        raise ReportError("쓸 수 있는 작업이 없습니다 (제목과 목표가 빠졌습니다)")
    return tasks, human[:12]


def plan_tasks(settings: Settings, project: Path, rid: str, ai: Optional[str] = None, *, providers: Optional[dict[str, Provider]] = None) -> dict:
    from .jsonutil import extract_json
    from .providers import build_providers, default_ais

    project = Path(project).resolve()
    view = load_report(project, settings.state_dir, rid)
    if view is None or not view.get("markdown"):
        raise ReportError("나눌 보고서가 없습니다 (아직 작성 중이거나 실패한 보고서입니다).")
    providers = providers if providers is not None else build_providers(settings)
    order = [ai] if ai else [a for a in SYNTH_ORDER if a in providers and a != "ollama"] + [a for a in default_ais(providers) if a not in SYNTH_ORDER]
    order = [a for a in order if a in providers] or list(providers)[:1]
    if not order:
        raise ValueError("no AI CLI is installed")
    rdir = project / settings.state_dir / "reports" / rid
    log = _Log(rdir / "events.jsonl")
    ctx = ""
    if view.get("context"):
        try:
            ctx = project_context(project)
        except Exception:
            ctx = ""
    sandbox = tempfile.TemporaryDirectory(prefix="solomon-tasks-")
    gitops.git(["init", "-q"], sandbox.name)
    last = "AI가 응답하지 못했습니다"
    try:
        for who in order:
            log.event("tasks_start", provider=who)
            try:
                res = providers[who].run(split_prompt(view.get("question", ""), view["markdown"], ctx), cwd=sandbox.name, write=False,
                                         timeout=float(settings.timeouts.get("analysis", 900.0)), no_output_timeout=None)
                tasks, human = _validate_tasks(extract_json(res.text))
            except (QuotaExhausted, ProviderError, ValueError) as exc:  # ValueError covers ReportError and bad JSON
                last = f"{who}: {exc}"[:300]
                log.event("tasks_end", provider=who, ok=False, error=last)
                continue
            out = {"report_id": rid, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "provider": who, "tasks": tasks, "human_steps": human}
            (rdir / "tasks.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
            log.event("tasks_end", provider=who, ok=True, count=len(tasks))
            return out
    finally:
        sandbox.cleanup()
    raise ReportError("작업으로 나누지 못했습니다: " + last)


def load_tasks(project: Path, state_dir: str, rid: str) -> Optional[dict]:
    if not REPORT_ID.match(rid or ""):
        raise ReportError("보고서 번호가 올바르지 않습니다.")
    try:
        data = json.loads((Path(project) / state_dir / "reports" / rid / "tasks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("tasks"), list) else None


def save_task_goal(project: Path, state_dir: str, rid: str, task_id: int, goal: str) -> dict:
    data = load_tasks(project, state_dir, rid)
    goal = (goal or "").strip()
    if data is None:
        raise ReportError("작업 목록이 없습니다.")
    if len(goal) < 20 or len(goal) > MAX_TASK_GOAL:
        raise ReportError(f"목표는 20~{MAX_TASK_GOAL}자여야 합니다.")
    task = next((t for t in data["tasks"] if t.get("id") == task_id), None)
    if task is None:
        raise ReportError("그 작업이 없습니다.")
    task["goal"] = goal
    (Path(project) / state_dir / "reports" / rid / "tasks.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def task_marker(rid: str, task_id: int, total: int) -> str:
    return f"[보고서 {rid} · 단계 {task_id}/{total}]"


def task_goal_text(project: Path, state_dir: str, rid: str, task_id: int) -> str:
    """The text a builder run gets for one task: a marker (so the screen can follow the run back to its task), the goal, and the check."""
    data = load_tasks(project, state_dir, rid)
    if data is None:
        raise ReportError("작업 목록이 없습니다. 먼저 '작은 작업으로 나누기'를 하세요.")
    task = next((t for t in data["tasks"] if t.get("id") == task_id), None)
    if task is None:
        raise ReportError("그 작업이 없습니다.")
    parts = [task_marker(rid, task_id, len(data["tasks"])) + " " + task["title"], "", task["goal"]]
    if task.get("files"):
        parts += ["", "건드릴 가능성이 큰 파일: " + ", ".join(task["files"])]
    if task.get("verify"):
        parts += ["", "확인 방법: " + task["verify"]]
    parts += ["", "이 작업 하나만 구현하세요. 보고서의 다른 단계는 별도 작업으로 나중에 진행합니다."]
    return "\n".join(parts)


def set_task_target(project: Path, state_dir: str, rid: str, target: str) -> dict:
    """Which project the tasks are BUILT in. A report may live in one project (where it was asked) while its tasks change another one (the bot)."""
    data = load_tasks(project, state_dir, rid)
    if data is None:
        raise ReportError("작업 목록이 없습니다.")
    data["target"] = str(target)
    (Path(project) / state_dir / "reports" / rid / "tasks.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def task_statuses(project: Path, state_dir: str, rid: str, tasks: list[dict], target: Optional[str] = None) -> dict[int, dict]:
    """Where every task stands, read back from the runs that carry its marker: todo / running / passed (waiting to be applied) / applied / failed."""
    from . import manage
    from .status import load_runs

    where = Path(target or project)  # the runs of the tasks are in the project they were built in
    runs = load_runs(where, state_dir)
    marks = manage._load_marks(where, state_dir)
    batch_of: dict[str, str] = {}  # run id -> the chain (batch) that built it, so the screen can send the person to the right place to apply it
    for f in (where / state_dir / "batches").glob("*/batch.json"):
        try:
            for it in json.loads(f.read_text(encoding="utf-8")).get("items", []):
                if it.get("run_id"):
                    batch_of[it["run_id"]] = f.parent.name
        except (OSError, ValueError):
            continue
    out: dict[int, dict] = {}
    for t in tasks:
        mk = task_marker(rid, t["id"], len(tasks))
        mine = [r for r in runs if (r.goal or "").startswith(mk)]
        if not mine:
            out[t["id"]] = {"state": "todo", "run_id": None, "runs": 0}
            continue
        r = mine[-1]
        if not r.finished:
            st = "running"
        elif r.status == "PASSED":
            applied = (where / state_dir / "runs" / r.run_id / "applied.json").is_file()
            ms = manage._state_of(f"run:{r.run_id}", marks, applied)
            st = "applied" if ms in ("applied", "committed") else "discarded" if ms == "discarded" else "passed"
        else:
            st = "failed"
        out[t["id"]] = {"state": st, "run_id": r.run_id, "runs": len(mine), "status": r.status, "batch_id": batch_of.get(r.run_id, "")}
    return out
