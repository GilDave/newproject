"""`solomon status`: summarize runs from their events.jsonl (read-only)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

_TS = "%Y-%m-%dT%H:%M:%S"

_STAGE_BY_EVENT = {
    "order": "계획 완료",
    "scope_violation": "범위 위반 (재시도 준비)",
    "verify": "검수",
    "review": "판정",
    "quota": "한도 소진",
}
def _base(role: str) -> str:
    """planner2 / builder3 / reviewer2 ... belong to the same stage as planner / builder / reviewer."""
    return role.rstrip("23") if role[:-1] in ("planner", "builder", "reviewer") else role


_STAGE_BY_ROLE_START = {"planner": "계획", "builder": "구현", "reviewer": "검수", "arbiter": "결정"}
_STAGE_BY_ROLE_END = {"planner": "계획 완료", "builder": "범위 검사와 테스트", "reviewer": "판정", "arbiter": "결정 완료"}


@dataclass
class ProviderStats:
    calls: int = 0
    failures: int = 0
    cli_sec: float = 0.0
    tokens: int = 0  # exact where the CLI reported it, estimate otherwise
    cache_read: int = 0  # part of `tokens` that was cached input (Claude only)
    estimated_calls: int = 0  # how many calls had to be estimated


@dataclass
class RunSummary:
    run_id: str
    goal: str = ""
    status: str = "진행 중 (또는 비정상 종료)"
    finished: bool = False
    summary: str = ""
    attempts: int = 0
    stage: str = "시작"
    started: Optional[datetime] = None
    ended: Optional[datetime] = None
    providers: dict[str, ProviderStats] = field(default_factory=dict)
    timeline: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stage_key: str = "start"  # start | plan | build | scope | test | review | decide | quota | done
    roles: dict = field(default_factory=dict)
    calls: list[dict] = field(default_factory=list)  # one entry per finished or failed AI call
    active: Optional[dict] = None  # the call in flight right now: {role, provider, since}
    quota_providers: list[str] = field(default_factory=list)
    attempt_details: list[dict] = field(default_factory=list)  # one entry per attempt: builder, changes, scope, tests, review

    def elapsed_sec(self, now: Optional[datetime] = None) -> Optional[float]:
        if not self.started:
            return None
        return ((self.ended or now or datetime.now()) - self.started).total_seconds()

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id, "goal": self.goal, "status": self.status, "finished": self.finished,
            "summary": self.summary, "attempts": self.attempts, "stage": self.stage, "stage_key": self.stage_key,
            "started": self.started.isoformat() if self.started else None,
            "elapsed_sec": self.elapsed_sec(), "roles": self.roles, "warnings": self.warnings,
            "timeline": self.timeline, "calls": self.calls, "active": self.active,
            "quota_providers": self.quota_providers, "attempt_details": self.attempt_details,
            "providers": {
                name: {"calls": st.calls, "failures": st.failures, "cli_sec": round(st.cli_sec, 1), "tokens": st.tokens,
                       "cache_read": st.cache_read, "estimated_calls": st.estimated_calls}
                for name, st in self.providers.items()
            },
        }


def _parse_ts(value: object) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value), _TS)
    except ValueError:
        return None


def _stats(run: RunSummary, name: str) -> ProviderStats:
    return run.providers.setdefault(name, ProviderStats())


def summarize_run(run_id: str, events: list[dict]) -> RunSummary:
    run = RunSummary(run_id=run_id)
    last_start: dict[str, dict] = {}
    pending: dict[str, dict] = {}
    attempt: Optional[dict] = None
    for ev in events:
        kind = ev.get("event")
        ts = _parse_ts(ev.get("ts"))
        clock = ts.strftime("%H:%M:%S") if ts else "--:--:--"
        if kind == "run_start":
            run.goal = str(ev.get("goal", ""))
            run.started = ts
            run.warnings = [str(w) for w in ev.get("warnings", [])]
            run.roles = dict(ev.get("roles") or {})
            run.timeline.append(f"{clock}  시작")
        elif kind == "attempt_start":
            run.attempts = int(ev.get("attempt", run.attempts + 1))
            attempt = {"attempt": run.attempts, "builder": None, "changes": [], "violations": [], "verify": None, "visual": None, "review": None, "arbiter": None}
            run.attempt_details.append(attempt)
            run.timeline.append(f"{clock}  시도 {run.attempts}")
        elif kind == "call_start":
            role = str(ev.get("role"))
            last_start[role] = ev
            pending[role] = ev
            run.active = {"role": role, "provider": str(ev.get("provider")), "since": str(ev.get("ts"))}
            run.stage = _STAGE_BY_ROLE_START.get(_base(role), run.stage)
            run.stage_key = {"planner": "plan", "builder": "build", "reviewer": "review", "arbiter": "decide"}.get(_base(role), run.stage_key)
        elif kind == "call_end":
            role, provider = str(ev.get("role")), str(ev.get("provider"))
            st = _stats(run, provider)
            st.calls += 1
            st.cli_sec += float(ev.get("duration_sec") or 0)
            exact = ev.get("tokens")
            call_tokens = ""
            if isinstance(exact, dict) and exact.get("total"):
                st.tokens += int(exact["total"])
                st.cache_read += int(exact.get("cache_read") or 0)
                call_tokens = f" · 토큰 {int(exact['total']):,}"
                if exact.get("turns"):
                    call_tokens += f" · {int(exact['turns'])}턴"
            else:
                est = ev.get("tokens_est")
                if isinstance(est, dict):
                    st.tokens += int(est.get("in") or 0) + int(est.get("out") or 0)
                else:  # runs recorded before token logging: characters only
                    start = last_start.get(role, {})
                    st.tokens += int((int(start.get("prompt_chars") or 0) + int(ev.get("output_chars") or 0)) / 3)
                st.estimated_calls += 1
                call_tokens = " · 토큰 추정"
            run.stage = _STAGE_BY_ROLE_END.get(_base(role), run.stage)
            run.timeline.append(f"{clock}  {role} · {provider} · {float(ev.get('duration_sec') or 0):.0f}초{call_tokens}")
            pending.pop(role, None)
            run.active = None
            if _base(role) == "builder" and (last_start.get(role) or {}).get("write"):  # a read-only builder slot is an advisor
                run.stage_key = "test"
                if attempt is not None:
                    attempt["builder"] = {"provider": provider, "sec": round(float(ev.get("duration_sec") or 0), 1),
                                          "tokens": int(exact["total"]) if isinstance(exact, dict) and exact.get("total") else None}
            run.calls.append({
                "at": clock, "role": role, "provider": provider, "sec": round(float(ev.get("duration_sec") or 0), 1),
                "tokens": int(exact["total"]) if isinstance(exact, dict) and exact.get("total") else None,
                "turns": int(exact["turns"]) if isinstance(exact, dict) and exact.get("turns") else None,
                "estimated": not (isinstance(exact, dict) and exact.get("total")), "ok": True,
            })
        elif kind == "call_error":
            pending.pop(str(ev.get("role")), None)
            run.active = None
            run.calls.append({"at": clock, "role": str(ev.get("role")), "provider": str(ev.get("provider")), "sec": 0,
                              "tokens": None, "turns": None, "estimated": False, "ok": False,
                              "error": str(ev.get("error", ""))[:140]})
            _stats(run, str(ev.get("provider"))).failures += 1
            run.timeline.append(f"{clock}  {ev.get('role')} 호출 실패 · {str(ev.get('error', ''))[:60]}")
        elif kind == "quota":
            run.stage_key = "quota"
            if str(ev.get("provider")) not in run.quota_providers:
                run.quota_providers.append(str(ev.get("provider")))
            run.stage = _STAGE_BY_EVENT["quota"]
            run.timeline.append(f"{clock}  한도 소진 · {ev.get('provider')}")
        elif kind == "changes":
            if attempt is not None:
                attempt["changes"] = list(ev.get("files") or [])
        elif kind == "scope_violation":
            if attempt is not None:
                attempt["violations"] = [str(v) for v in ev.get("violations", [])]
            run.stage_key = "scope"
            run.stage = _STAGE_BY_EVENT[kind]
            run.timeline.append(f"{clock}  범위 위반 {len(ev.get('violations', []))}건")
        elif kind == "verify":
            if attempt is not None:
                attempt["verify"] = {"passed": bool(ev.get("passed")), "skipped": bool(ev.get("skipped")),
                                     "exit_code": ev.get("exit_code"), "command": str(ev.get("command") or ""),
                                     "tail": str(ev.get("output_tail") or "")[-700:]}
            run.stage_key = "review"
            run.stage = _STAGE_BY_EVENT[kind]
            label = "생략" if ev.get("skipped") else ("통과" if ev.get("passed") else "실패")
            run.timeline.append(f"{clock}  테스트 {label}")
        elif kind == "visual":
            if attempt is not None:
                attempt["visual"] = {"ok": bool(ev.get("ok")), "broken": str(ev.get("broken") or ""), "infra": str(ev.get("infra") or ""),
                                     "shots": [str(x) for x in ev.get("shots", [])]}
            run.timeline.append(f"{clock}  화면 캡처 " + ("통과" if ev.get("ok") else "실패(화면 깨짐)" if ev.get("broken") else "건너뜀"))
        elif kind == "review":
            if attempt is not None:
                attempt["review"] = {"verdict": str(ev.get("verdict")), "issues": [str(i) for i in ev.get("issues", [])]}
            run.stage_key = "decide"
            run.stage = _STAGE_BY_EVENT[kind]
            run.timeline.append(f"{clock}  검수 {ev.get('verdict')}")
        elif kind == "arbiter":
            if attempt is not None:
                attempt["arbiter"] = {"provider": str(ev.get("provider")), "decision": str(ev.get("decision")), "final": str(ev.get("final")),
                                      "confidence": ev.get("confidence"), "reason": str(ev.get("reason", ""))}
            run.stage_key = "decide"
            run.stage = "결정"
            run.timeline.append(f"{clock}  결정권자 {ev.get('provider')}: {ev.get('final')}"
                                + (f" (원래 {ev.get('decision')})" if ev.get("final") != ev.get("decision") else "") + f" · 확신 {ev.get('confidence')}")
        elif kind == "order":
            run.stage_key = "plan"
            run.stage = _STAGE_BY_EVENT[kind]
            run.timeline.append(f"{clock}  작업 지시서 생성")
        elif kind == "run_end":
            run.finished = True
            run.status = str(ev.get("status", ""))
            run.summary = str(ev.get("summary", ""))
            run.attempts = int(ev.get("attempts", run.attempts))
            run.ended = ts
            run.stage = "완료"
            run.stage_key = "done"
            run.active = None
            run.timeline.append(f"{clock}  종료 {run.status}")
    return run


def load_runs(project: Path, state_dir: str = ".solomon") -> list[RunSummary]:
    runs_dir = project / state_dir / "runs"
    if not runs_dir.is_dir():
        return []
    runs = []
    for d in sorted(runs_dir.iterdir()):
        log = d / "events.jsonl"
        if not log.is_file():
            continue
        events = []
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a half-written last line while a run is in progress
        runs.append(summarize_run(d.name, events))
    return runs


def _clock(seconds: Optional[float]) -> str:
    if seconds is None:
        return "--:--"
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def _tokens(st: ProviderStats) -> str:
    if st.calls == 0:
        return "0"
    mark = "" if st.estimated_calls == 0 else "~"
    note = "정확" if st.estimated_calls == 0 else ("추정" if st.estimated_calls == st.calls else "일부 추정")
    cache = f", 캐시 읽기 {st.cache_read:,} 포함" if st.cache_read else ""
    return f"{mark}{st.tokens:,} ({note}{cache})"


def render(runs: list[RunSummary], project: Path, limit: int = 5) -> str:
    lines = [f"Solomon 진행 현황 · {project}"]
    if not runs:
        lines.append("아직 실행 기록이 없습니다.  solomon run \"목표\" --project <경로>")
        return "\n".join(lines)
    lines.append("")
    lines.append("최근 실행")
    for r in reversed(runs[-limit:]):
        goal = r.goal if len(r.goal) <= 40 else r.goal[:40] + "…"
        lines.append(f"  {r.run_id}  {r.status}  시도 {r.attempts}  {_clock(r.elapsed_sec())}  {goal}")
    cur = runs[-1]
    lines += ["", f"선택한 실행: {cur.run_id}", f"  목표:   {cur.goal}", f"  상태:   {cur.status}  (단계: {cur.stage}, 시도 {cur.attempts})", f"  경과:   {_clock(cur.elapsed_sec())}"]
    if cur.summary:
        lines.append(f"  요약:   {cur.summary[:200]}")
    for w in cur.warnings:
        lines.append(f"  주의:   {w}")
    lines += ["", "AI별 작업량"]
    if not cur.providers:
        lines.append("  (아직 호출 없음)")
    for name, st in cur.providers.items():
        fail = f" · 실패 {st.failures}" if st.failures else ""
        lines.append(f"  {name:<8} 호출 {st.calls}회 · CLI {st.cli_sec:.0f}초{fail} · 토큰 {_tokens(st)}")
    lines += ["", "진행 기록"] + [f"  {t}" for t in cur.timeline]
    return "\n".join(lines)
