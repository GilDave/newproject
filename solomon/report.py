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
