"""The pipeline: PLAN -> BUILD -> scope check -> VERIFY -> cross REVIEW -> decide.

The final decision is deterministic in the MVP: PASS only when the scope guard is
clean, the tests pass and the reviewer (a different provider than the builder)
says PASS. A reviewer that fails or returns garbage counts as FAIL (fail-closed).
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import quota as quota_mod, arbiter as arbiter_mod, context, gitops, policy as policy_mod, prompts, scope_guard, vision as vision_mod, visual as visual_mod
from .jsonutil import extract_json
from .providers import Provider, ProviderError, ProviderResult, QuotaExhausted, build_providers
from .runlog import RunLog
from .schemas import Order, ReviewResult
from .tokens import estimate_tokens
from .settings import ROLES, ConfigError, Settings
from .verifier import VerifyResult, verify


REVIEW_RETRIES = 2  # extra asks when the reviewer gives no verdict at all


class BlockedByQuota(Exception):
    def __init__(self, provider: str, detail: str):
        super().__init__(f"{provider}: subscription usage limit reached ({detail})")
        self.provider = provider


class PlanError(ValueError):
    pass


@dataclass
class RunResult:
    run_id: str
    status: str  # PASSED | FAILED | BLOCKED_QUOTA | STOPPED | ERROR
    summary: str
    run_dir: str
    attempts: int = 0
    patch_path: Optional[str] = None
    commit: Optional[str] = None  # set when an on_pass hook committed the result
    reduced_independence: bool = False
    warnings: list[str] = field(default_factory=list)
    arbiter: Optional[dict] = None  # the decision-maker's answer on the accepted result (decision, final, confidence, reason)
    needs_human: bool = False  # the arbiter (or the visual gate) asked a person to look before this is applied
    visual: Optional[dict] = None  # the screen-capture gate: pictures taken, who looked at them


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        providers: Optional[dict[str, Provider]] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        quota_board: Optional["quota_mod.QuotaBoard"] = None,
    ):
        self.s = settings
        self.board = quota_board if quota_board is not None else quota_mod.QuotaBoard()
        self._base_roles = dict(settings.roles)
        self._builder_now: Optional[str] = None
        self.providers = providers if providers is not None else build_providers(settings)
        self._sleep = sleep
        self._clock = clock
        self._tokens = 0
        for role, name in settings.roles.items():
            if name not in self.providers:
                raise ConfigError(f"role '{role}' uses unknown provider '{name}' (known: {sorted(self.providers)})")
        if not self._can_write(settings.roles["builder"]):
            raise ConfigError(
                f"'{settings.roles['builder']}' cannot be the builder (it is limited to read-only roles: planner, reviewer)"
            )
        self.reduced_independence = settings.roles["builder"] == settings.roles["reviewer"]
        if self.reduced_independence and not settings.allow_same_provider:
            raise ConfigError(
                "builder and reviewer must use different providers "
                "(set loop.allow_same_provider = true to accept REDUCED_INDEPENDENCE)"
            )
        reviewers = [settings.roles[r] for r in ("reviewer", "reviewer2", "reviewer3") if settings.roles.get(r)]
        for r, name in self._builders()[0]:
            if not settings.allow_same_provider and all(x == name for x in reviewers):
                raise ConfigError(f"every reviewer is the same AI as the builder '{r}' ({name}): add a reviewer that differs from it")

    def _can_write(self, name: str) -> bool:
        return bool(getattr(self.providers[name], "supports_write", True))

    def _builders(self) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """(write-capable builders in rotation order, read-only advisors) from builder / builder2 / builder3."""
        pool = [(r, self.s.roles[r]) for r in ("builder", "builder2", "builder3") if self.s.roles.get(r)]
        return [x for x in pool if self._can_write(x[1])], [x for x in pool if not self._can_write(x[1])]

    # ------------------------------------------------------------------ calls
    DEFAULT_FAILOVER = {"builder": ("codex", "claude"), "planner": ("claude", "codex", "antigravity"),
                        "reviewer": ("claude", "codex", "antigravity"), "arbiter": ()}  # the arbiter fails safe (a person decides)

    def _candidates(self, role: str, current: str) -> list[str]:
        """AIs that may take over `role` from `current`, best first: signed in, not out of usage, allowed for the stage."""
        base = role.rstrip("23") if role[:-1] in ("planner", "builder", "reviewer") else role
        order = list(self.s.failover.get(base) or self.DEFAULT_FAILOVER.get(base, ()))
        now = self._clock()
        out = []
        for name in order:
            prov = self.providers.get(name)
            if prov is None or name == current or self.board.exhausted(name, now):
                continue
            if getattr(prov, "locate", None) and not prov.locate():
                continue
            if base == "builder" and not self._can_write(name):
                continue
            if not self.s.allow_same_provider:
                reviewers = [self.s.roles[r] for r in ("reviewer", "reviewer2", "reviewer3") if self.s.roles.get(r)]
                if base == "builder" and reviewers and all(r == name for r in reviewers):
                    continue  # the new builder would be its own only reviewer
                if base == "reviewer" and name == self._builder_now:
                    continue
                if base == "arbiter" and name == self._builder_now:
                    continue
            out.append(name)
        return out

    def _switch(self, role: str, current: str, log: RunLog, reason: str) -> bool:
        """Plan B: put the next available AI on this stage for the rest of the run. False when nobody can take over."""
        if not self.s.auto_failover:
            return False
        cands = self._candidates(role, current)
        if not cands:
            return False
        self.s.roles[role] = cands[0]
        if role.startswith("builder"):
            self._builder_now = cands[0]
        log.event("failover", role=role, **{"from": current, "to": cands[0]}, reason=reason)
        return True

    def _apply_board(self, log: RunLog) -> None:
        """A new run starts from the configured roles, then routes around every AI the board knows is out of usage."""
        self.s.roles.clear()
        self.s.roles.update(self._base_roles)
        for role in list(self.s.roles):
            name = self.s.roles[role]
            if self.board.exhausted(name, self._clock()):
                self._switch(role, name, log, "limit already known")

    def _call(self, role: str, prompt: str, cwd: Path, write: bool, log: RunLog, images: Optional[list[str]] = None) -> ProviderResult:
        waits = 0
        while True:
            name = self.s.roles[role]
            if self.s.auto_failover and self.board.exhausted(name, self._clock()) and self._switch(role, name, log, "limit already known"):
                continue
            provider = self.providers[self.s.roles[role]]
            log.event("call_start", role=role, provider=provider.name, write=write, prompt_chars=len(prompt))
            try:
                extra = {"images": images} if images else {}
                res = provider.run(
                    prompt,
                    cwd=str(cwd),
                    write=write,
                    timeout=self.s.timeouts.get(role, self.s.timeouts["reviewer"]),
                    no_output_timeout=self.s.timeouts["no_output"],
                    **extra,
                )
            except QuotaExhausted as exc:
                until = self.board.mark(provider.name, str(exc), self._clock(), self.s.quota_cooldown_min)
                log.event("quota", role=role, provider=provider.name, detail=str(exc), waits=waits, until=until)
                if self._switch(role, provider.name, log, "usage limit"):
                    continue  # plan B takes the very same call
                if self.s.auto_failover:  # nobody else can do it: wait for the earliest reset instead of ending the night
                    resets = [u for u in (self.board.until(n, self._clock()) for n in set(self.s.roles.values())) if u] or [until]
                    if waits < max(self.s.quota_max_waits, 8):
                        waits += 1
                        wait = max(60.0, min(min(resets) - self._clock() + 30, 3600.0))
                        log.event("quota_wait", role=role, provider=provider.name, seconds=round(wait), waits=waits)
                        self._sleep(wait)
                        continue
                elif self.s.on_quota == "wait" and waits < self.s.quota_max_waits:
                    waits += 1
                    self._sleep(self.s.quota_wait_sec)
                    continue
                raise BlockedByQuota(provider.name, str(exc)) from exc
            except ProviderError as exc:
                log.event("call_error", role=role, provider=provider.name, error=str(exc))
                raise
            self._tokens += int((res.tokens or {}).get("total") or (estimate_tokens(prompt) + estimate_tokens(res.text)))
            log.event(
                "call_end",
                role=role,
                provider=provider.name,
                duration_sec=res.duration_sec,
                prompt_chars=len(prompt),
                output_chars=len(res.text),
                tokens=res.tokens,  # exact, reported by the CLI (None if it did not report)
                tokens_est={"in": estimate_tokens(prompt), "out": estimate_tokens(res.text)},
            )
            return res

    def _helper(self, role: str, prompt: str, cwd: Path, log: RunLog) -> Optional[str]:
        """An advisory call (plan critic, build advisor): its failure or quota never stops the run."""
        try:
            return self._call(role, prompt, cwd, False, log).text
        except (BlockedByQuota, ProviderError) as exc:
            log.event("helper_skipped", role=role, provider=self.s.roles.get(role), reason=str(exc)[:200])
            return None

    # ------------------------------------------------------------------- plan
    def _plan_review(self, goal: str, order: Order, project: Path, log: RunLog) -> Order:
        notes: list[str] = []
        for role in ("planner2", "planner3"):
            if not self.s.roles.get(role):
                continue
            text = self._helper(role, prompts.plan_critic(goal, order), project, log)
            if text is None:
                continue
            try:
                data = extract_json(text)
            except ValueError:
                log.event("plan_review", role=role, provider=self.s.roles[role], verdict="UNPARSEABLE", concerns=[], added=[])
                continue
            added = []
            for c in (data.get("extra_acceptance_criteria") or [])[:3]:
                c = str(c).strip()[:240]
                if c and c not in order.acceptance_criteria and c not in added:
                    added.append(c)
            concerns = [str(c).strip()[:240] for c in (data.get("concerns") or [])[:3] if str(c).strip()]
            order.acceptance_criteria = list(order.acceptance_criteria) + added  # criteria only: scope and limits are never widened
            if concerns:
                notes.append(f"[{self.s.roles[role]}] " + " / ".join(concerns))
            log.event("plan_review", role=role, provider=self.s.roles[role], verdict=str(data.get("verdict", ""))[:10],
                      concerns=concerns, added=added)
        if notes:
            order.context = (order.context + "\nPlan reviewers' concerns: " + " | ".join(notes))[:2000]
        return order

    def _plan(self, goal: str, project: Path, log: RunLog) -> Order:
        every = gitops.list_files(project, limit=5000)
        base = prompts.planner(
            goal, every[:300], self.s.max_files_changed, self.s.max_diff_lines,
            referenced=context.referenced_files(goal, every, project),
        )
        error: Optional[str] = None
        for _ in range(2):
            prompt = base if error is None else f"{base}\nYour previous answer was invalid: {error}\nReturn only valid JSON."
            res = self._call("planner", prompt, project, False, log)
            try:
                order = Order.from_dict(extract_json(res.text), goal, self.s.max_files_changed, self.s.max_diff_lines)
            except ValueError as exc:
                error = str(exc)
                log.event("plan_invalid", error=error)
                continue
            order = self._plan_review(goal, order, project, log)
            pol = policy_mod.load(project, self.s.state_dir)
            self._policy = pol
            if pol["protect"]:  # the project's own protected paths: no plan may drop them
                order.protected_paths = list(dict.fromkeys(order.protected_paths + pol["protect"]))
            if pol["shadow"]:
                self.s.arbiter_shadow = True
            log.event("order", order=order.to_dict())
            return order
        raise PlanError(f"planner did not produce a valid ORDER: {error}")

    # ----------------------------------------------------------------- review
    def _can_see(self, name: str) -> bool:
        prov = self.providers.get(name)
        return bool(prov is not None and getattr(prov, "image_capable", False) and vision_mod.verified(name, prov))

    def _review_role(self, role: str, order: Order, wt: Path, verification: VerifyResult, warnings: list[str],
                     log: RunLog, attempt: int, visual: Optional["visual_mod.VisualResult"] = None) -> ReviewResult:
        """One reviewer's verdict. No verdict (timeout, crash, garbage) is asked again about the SAME code."""
        summary = (
            "tests skipped (UNVERIFIED)"
            if verification.skipped
            else f"command: {verification.command}\nexit code: {verification.exit_code}\n{verification.output_tail[-1500:]}"
        )
        prompt = prompts.reviewer(order, gitops.full_diff(wt), summary, warnings)
        images: Optional[list[str]] = None
        if visual and visual.ok and visual.shots:
            if self._can_see(self.s.roles[role]):
                images = [str(p.resolve()) for p in visual.shots]
                prompt += prompts.visual_note(images, can_see=True)
                self._visual_seers.append(self.s.roles[role])
            else:
                prompt += prompts.visual_note([], can_see=False)
        last = ReviewResult("FAIL", ["no verdict"], error=True)
        for retry in range(1 + REVIEW_RETRIES):
            if retry:
                log.event("review_retry", attempt=attempt, role=role, retry=retry, reason=last.issues[0] if last.issues else "")
            try:
                res = self._call(role, prompt, wt, False, log, images=images)
            except BlockedByQuota:
                raise
            except ProviderError as exc:
                last = ReviewResult("FAIL", [f"reviewer unavailable, treated as FAIL: {exc}"], error=True)
                continue
            try:
                return ReviewResult.from_dict(extract_json(res.text))
            except ValueError as exc:
                last = ReviewResult("FAIL", [f"unparseable review, treated as FAIL: {exc}"], error=True)
        return last

    def _review(self, order: Order, wt: Path, verification: VerifyResult, warnings: list[str], log: RunLog,
                attempt: int = 0, builder_name: Optional[str] = None, visual: Optional["visual_mod.VisualResult"] = None) -> ReviewResult:
        """Cross-check: the reviewer, and the second reviewer when configured. Every reviewer must say PASS.
        The second one is only asked about code the first already accepted (time is money)."""
        agg = ReviewResult("PASS", [], "")
        builder_name = builder_name or self.s.roles["builder"]
        self._last_reviews = []
        self._visual_seers = []
        roles = [r for r in ("reviewer", "reviewer2", "reviewer3") if self.s.roles.get(r)]
        if not self.s.allow_same_provider:
            skipped = [r for r in roles if self.s.roles[r] == builder_name]
            roles = [r for r in roles if r not in skipped]
            for r in skipped:
                log.event("review_skipped_self", role=r, provider=builder_name, attempt=attempt)
            if not roles:
                return ReviewResult("FAIL", [f"no reviewer is independent of the builder ({builder_name})"], error=True)
        for role in roles:
            who = self.s.roles[role]
            one = self._review_role(role, order, wt, verification, warnings, log, attempt, visual)
            log.event("review_role", role=role, provider=who, verdict=one.verdict, error=one.error, issues=one.issues, saw_images=who in self._visual_seers)
            self._last_reviews.append({"role": role, "provider": who, "verdict": one.verdict, "issues": list(one.issues)})
            if one.error:
                return ReviewResult("FAIL", [f"[{who}] {i}" for i in one.issues], error=True)
            if not agg.summary:
                agg.summary = one.summary
            if one.verdict != "PASS":
                agg.verdict = "FAIL"
                agg.issues += [f"[{who}] {i}" for i in one.issues]
                break
        return agg

    # -------------------------------------------------------------------- run
    def run(
        self,
        goal: str,
        project: str | Path,
        keep_worktree: bool = False,
        base_ref: str = "HEAD",
        on_pass: Optional[Callable[[Path, "RunResult"], Optional[str]]] = None,
    ) -> RunResult:
        """base_ref: start from this commit or branch (a batch builds each fix on the previous one).
        on_pass(worktree, result) runs while the passed worktree still exists and may return a commit id."""
        self._on_pass = on_pass
        self._tokens = 0
        project = Path(project).resolve()
        gitops.ensure_repo(project)
        run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        state = project / self.s.state_dir
        run_dir = state / "runs" / run_id
        run_dir.mkdir(parents=True)
        gitops.exclude_from_git(project, self.s.state_dir)
        log = RunLog(run_dir / "events.jsonl")
        result = RunResult(
            run_id=run_id, status="ERROR", summary="", run_dir=str(run_dir), reduced_independence=self.reduced_independence
        )
        if self.reduced_independence:
            result.warnings.append("REDUCED_INDEPENDENCE: builder and reviewer use the same provider")
        if base_ref == "HEAD" and gitops.is_dirty(project):
            if self.s.include_uncommitted:
                try:
                    base_ref, changed = gitops.snapshot_worktree(project)
                    result.warnings.append(f"SNAPSHOT: worked on the folder as it is on disk, including {len(changed)} uncommitted file(s)")
                except gitops.GitError as exc:
                    result.warnings.append(f"project has uncommitted changes; snapshot failed ({exc}), using the committed state")
            else:
                result.warnings.append("project has uncommitted changes; they are NOT part of the isolated worktree")
        self._apply_board(log)
        log.event("run_start", goal=goal, roles=self.s.roles, warnings=result.warnings)

        wt = state / "worktrees" / run_id
        created = False
        try:
            order = self._plan(goal, project, log)
            gitops.create_worktree(project, wt, base_ref)
            created = True
            self._build_loop(order, wt, run_dir, result, log)
        except KeyboardInterrupt:
            result.status, result.summary = "STOPPED", "사용자가 중지했습니다"
        except BlockedByQuota as exc:
            result.status, result.summary = "BLOCKED_QUOTA", str(exc)
        except (PlanError, ProviderError, gitops.GitError) as exc:
            result.status, result.summary = "ERROR", str(exc)
        finally:
            if created and not keep_worktree:
                gitops.remove_worktree(project, wt)
        log.event("run_end", status=result.status, summary=result.summary, attempts=result.attempts)
        (run_dir / "result.json").write_text(json.dumps(asdict(result), indent=2, ensure_ascii=False), encoding="utf-8")
        return result

    def _arbitrate(self, order: Order, wt: Path, changes: list, verification: VerifyResult, double_passed: Optional[bool],
                   history: list[str], attempt: int, builder_name: str, rejects: int, log: RunLog) -> Optional[arbiter_mod.Decision]:
        """The arbiter's answer on a result that passed every gate, or None when no arbiter is configured."""
        name = self.s.roles.get("arbiter")
        if not name:
            return None
        risky = arbiter_mod.risk_paths([c.path for c in changes])
        if name == builder_name and not self.s.allow_same_provider:
            log.event("arbiter_skipped_self", provider=name, attempt=attempt)
            dec = arbiter_mod.unavailable(f"{name}가 이번 시도의 구현자라 스스로 결정하지 않습니다")
        else:
            evidence = arbiter_mod.build_evidence(order, attempt, changes, verification, double_passed, getattr(self, "_last_reviews", []),
                                                  history, gitops.full_diff(wt), self.s.arbiter_diff_chars)
            prompt = arbiter_mod.prompt(evidence)
            dec = None
            err = ""
            for retry in range(1 + REVIEW_RETRIES):
                try:
                    res = self._call("arbiter", prompt, wt, False, log)
                except (BlockedByQuota, ProviderError) as exc:
                    err = str(exc)
                    break  # the arbiter is down: do not hammer it, hand the decision to a person
                try:
                    dec = arbiter_mod.parse(res.text)
                    break
                except ValueError as exc:
                    err = f"형식 오류: {exc}"
                    log.event("arbiter_retry", attempt=attempt, retry=retry + 1, reason=str(exc)[:160])
            if dec is None:
                dec = arbiter_mod.unavailable(err or "응답 없음")
            else:
                dec = arbiter_mod.apply_policy(dec, self.s.arbiter_min_confidence, rejects, self.s.arbiter_max_rejects, risky)
        log.event("arbiter", attempt=attempt, provider=name, **dec.to_dict(), risky=risky)
        return dec

    def _visual_gate(self, wt: Path, run_dir: Path, attempt: int, verification: VerifyResult, log: RunLog) -> Optional["visual_mod.VisualResult"]:
        """Screenshots of the candidate, when the project defines a capture command and the tests passed (no point photographing a failure)."""
        cfg = (getattr(self, "_policy", None) or {}).get("visual")
        if not cfg or not verification.passed:
            return None
        res = visual_mod.run_capture(cfg, wt, run_dir / "shots" / f"attempt-{attempt}")
        gitops.clean_untracked(wt)
        log.event("visual", attempt=attempt, ok=res.ok, broken=res.broken, infra=res.infra, shots=[p.name for p in res.shots])
        return res

    def _shadow_judge(self, order: Order, wt: Path, changes: list, verification: VerifyResult, double_passed: Optional[bool],
                      history: list[str], attempt: int, builder_name: str, log: RunLog) -> None:
        """Shadow mode: the arbiter judges EVERY attempt (green or not) but nothing it says is obeyed.
        The answer is recorded next to the measured facts, so it can be scored against what really happened."""
        name = self.s.roles.get("arbiter")
        if not name or (name == builder_name and not self.s.allow_same_provider):
            return
        risky = arbiter_mod.risk_paths([c.path for c in changes])
        evidence = arbiter_mod.build_evidence(order, attempt, changes, verification, double_passed, getattr(self, "_last_reviews", []),
                                              history, gitops.full_diff(wt), self.s.arbiter_diff_chars, shadow=True)
        dec, err = None, ""
        for retry in range(1 + REVIEW_RETRIES):
            try:
                res = self._call("arbiter", arbiter_mod.prompt(evidence, shadow=True), wt, False, log)
            except (BlockedByQuota, ProviderError) as exc:
                err = str(exc)
                break
            try:
                dec = arbiter_mod.parse(res.text)
                break
            except ValueError as exc:
                err = f"형식 오류: {exc}"
        votes = [{"provider": r["provider"], "verdict": r["verdict"]} for r in getattr(self, "_last_reviews", [])]
        log.event("arbiter_shadow", attempt=attempt, provider=name, decision=dec.decision if dec else "NONE",
                  confidence=dec.confidence if dec else 0.0, reason=(dec.reason if dec else err)[:300],
                  tests="skipped" if verification.skipped else "pass" if verification.passed else "fail",
                  double=double_passed, votes=votes, risky=risky, files=len(changes),
                  lines=sum(c.added + c.removed for c in changes), error=err[:200] if not dec else "")

    def _budget_stop(self, attempt: int, started: float) -> Optional[str]:
        s = self.s
        if s.max_attempts and attempt > s.max_attempts:
            return f"시도 상한({s.max_attempts}번)에 도달했습니다"
        if s.max_run_minutes and self._clock() - started > s.max_run_minutes * 60:
            return f"시간 예산({s.max_run_minutes:g}분)을 넘겼습니다"
        if s.max_tokens and self._tokens >= s.max_tokens:
            return f"토큰 예산({s.max_tokens:,})을 넘겼습니다 (사용 {self._tokens:,})"
        return None

    @staticmethod
    def _signature(problems: list[str]) -> str:
        """What a failure looks like, with the noise (numbers, paths, timings) taken out, to spot a repeated one."""
        text = " ".join(problems).lower()
        text = re.sub(r"/[\w./-]+", "<path>", text)
        text = re.sub(r"\d+(\.\d+)?", "#", text)
        return re.sub(r"\s+", " ", text).strip()[:400]

    def _build_loop(self, order: Order, wt: Path, run_dir: Path, result: RunResult, log: RunLog) -> None:
        s = self.s
        started = self._clock()
        feedback: Optional[str] = None
        reset_next = False
        history: list[str] = []
        signatures: list[str] = []
        restarts = 0
        writers, advisors = self._builders()
        rot = consec = 0  # which write-capable builder is on duty; consecutive failed attempts
        arb_rejects = 0  # how often the arbiter sent work back in this run
        advice: Optional[str] = None
        advise_next = True

        if s.baseline_check and s.test_command:
            base = verify(s, wt)
            gitops.clean_untracked(wt)
            log.event("baseline", passed=base.passed, exit_code=base.exit_code, command=base.command,
                      output_tail=base.output_tail[-1500:])
            if not base.passed:
                result.status = "ERROR"
                result.summary = ("수정하기 전의 코드에서도 테스트가 실패합니다. 테스트 환경 문제일 수 있어 AI를 호출하지 않고 "
                                  f"멈췄습니다 (토큰 절약). 명령: {base.command}\n{base.output_tail[-600:]}")
                return

        attempt = 0
        while True:
            attempt += 1
            stop = self._budget_stop(attempt, started)
            if stop:
                result.status = "FAILED"
                result.summary = f"{stop}; {result.summary}"
                return
            result.attempts = attempt
            if reset_next:
                gitops.reset_worktree(wt)
                reset_next = False
            log.event("attempt_start", attempt=attempt)

            def failed(problems: list[str], summary: str, reset: bool = False) -> Optional[str]:
                """Record a rejected attempt. Returns a reason to stop when the same failure keeps repeating."""
                nonlocal feedback, reset_next, restarts, rot, consec, advise_next
                result.summary = summary
                consec += 1
                switch = reset  # hand the work to the next builder after a CLI failure, a stall, or every second failure
                history.append(f"시도 {attempt}: {summary}")
                signatures.append(self._signature(problems))
                feedback = "\n\n".join(problems)
                reset_next = reset
                if len(signatures) >= s.stagnation_limit and len(set(signatures[-s.stagnation_limit:])) == 1:
                    restarts += 1
                    log.event("stagnation", attempt=attempt, restarts=restarts)
                    if restarts > s.max_restarts:
                        return f"같은 실패가 {s.stagnation_limit}번씩 {restarts}차례 반복돼 중단했습니다 (진전 없음)"
                    signatures.clear()
                    reset_next = True  # start over from clean code with a different approach
                    switch = True
                    advise_next = True
                    feedback += ("\n\nTHE SAME FAILURE REPEATED " + str(s.stagnation_limit) + " TIMES. The code was reset to its "
                                 "original state. Take a COMPLETELY DIFFERENT approach. Attempts so far:\n" + "\n".join(history[-6:]))
                elif len(history) > 1:
                    feedback += "\n\nEarlier attempts (do not repeat them):\n" + "\n".join(history[-6:-1])
                if switch or consec % 2 == 0:
                    rot += 1
                return None

            brole, bname = writers[rot % len(writers)]
            if advisors and advise_next:
                notes = []
                for arole, aname in advisors:
                    text = self._helper(arole, prompts.build_advisor(order, feedback), wt, log)
                    if text and text.strip():
                        notes.append(f"[{aname}] " + text.strip()[:1500])
                advice = "\n".join(notes) or None
                advise_next = False
            log.event("builder_pick", attempt=attempt, role=brole, provider=bname)
            self._builder_now = bname
            try:
                self._call(brole, prompts.builder(order, feedback, advice), wt, True, log)
                bname = self.s.roles[brole]  # plan B may have put another AI on the job during the call
                self._builder_now = bname
            except ProviderError as exc:
                if (why := failed([f"The builder CLI failed: {exc}"], f"builder CLI failed: {exc}", reset=True)):
                    result.status, result.summary = "FAILED", why
                    return
                continue

            changes = gitops.collect_changes(wt)
            if not changes:
                if (why := failed(["No files were changed. Implement the ORDER by editing files."], "builder made no changes")):
                    result.status, result.summary = "FAILED", why
                    return
                continue
            log.event("changes", count=len(changes), files=[
                {"path": c.path, "status": c.status, "added": c.added, "removed": c.removed} for c in changes[:40]])
            violations = scope_guard.check(order, changes)
            if violations:
                log.event("scope_violation", violations=violations)
                if (why := failed(["Scope violations (these changes were discarded):\n- " + "\n- ".join(violations)],
                                  "scope violation: " + "; ".join(violations), reset=True)):
                    result.status, result.summary = "FAILED", why
                    return
                continue

            verification = verify(s, wt)
            gitops.clean_untracked(wt)
            log.event("verify", passed=verification.passed, skipped=verification.skipped, exit_code=verification.exit_code,
                      command=verification.command, output_tail=verification.output_tail[-1500:])
            warnings = ["tests were not run: result is UNVERIFIED"] if verification.skipped else []
            visual = self._visual_gate(wt, run_dir, attempt, verification, log)
            if visual and visual.broken:  # the product does not even render: no reviewer needs to be asked
                self._last_reviews, self._visual_seers = [], []
                review = ReviewResult("FAIL", ["Screen capture: the page did not render properly: " + visual.broken])
            else:
                review = self._review(order, wt, verification, warnings, log, attempt, bname, visual)
            if review.error:
                (run_dir / "last_attempt.patch").write_text(gitops.full_diff(wt, binary=True), encoding="utf-8")
                result.status = "ERROR"
                result.summary = (f"검수 AI가 {1 + REVIEW_RETRIES}번 모두 판정을 내지 못해 중단했습니다. 구현은 다시 시키지 않았습니다 "
                                  f"(마지막 코드는 last_attempt.patch). 원인: {review.issues[0] if review.issues else '?'}")
                log.event("review", verdict="ERROR", issues=review.issues)
                return
            log.event("review", verdict=review.verdict, issues=review.issues)

            double_failed = False
            double_passed: Optional[bool] = None
            if verification.passed and review.verdict == "PASS" and s.double_check_tests and not verification.skipped:
                again = verify(s, wt)  # double check: a pass that does not repeat is not a pass
                gitops.clean_untracked(wt)
                log.event("double_check", passed=again.passed, exit_code=again.exit_code, output_tail=again.output_tail[-800:])
                double_failed = not again.passed
                double_passed = again.passed

            decision = None
            if s.arbiter_shadow:
                self._shadow_judge(order, wt, changes, verification, double_passed, history, attempt, bname, log)
            elif verification.passed and review.verdict == "PASS" and not double_failed:
                # every gate is green: only now is the arbiter asked, and it can only be stricter
                decision = self._arbitrate(order, wt, changes, verification, double_passed, history, attempt, bname, arb_rejects, log)
                if decision and decision.final == "REJECT":
                    arb_rejects += 1

            if verification.passed and review.verdict == "PASS" and not double_failed and not (decision and decision.final == "REJECT"):
                patch = run_dir / "result.patch"
                patch.write_text(gitops.full_diff(wt, binary=True), encoding="utf-8")
                if getattr(self, "_on_pass", None):
                    result.commit = self._on_pass(wt, result)
                result.status, result.patch_path = "PASSED", str(patch)
                result.summary = review.summary or "passed scope check, tests and independent review"
                if decision:
                    result.arbiter = decision.to_dict()
                    if decision.final == "NEED_HUMAN":
                        result.needs_human = True
                        result.warnings.append(f"ARBITER_NEEDS_HUMAN: {decision.note or decision.reason}")
                if verification.skipped:
                    result.warnings.append("no test command configured: passed without automatic tests")
                if visual:
                    rel = [str(p.relative_to(run_dir)) for p in visual.shots]
                    result.visual = {"attempt": attempt, "shots": rel, "viewed_by": sorted(set(self._visual_seers)), "infra": visual.infra}
                    if visual.infra:
                        result.needs_human = True
                        result.warnings.append(f"VISUAL_SKIPPED: 화면 캡처를 하지 못했습니다 — 사람이 직접 화면을 확인하세요 ({visual.infra})")
                    elif not self._visual_seers:
                        result.needs_human = True
                        result.warnings.append("VISUAL_UNVIEWED: 화면은 찍혔지만 이미지를 볼 수 있는 검수 AI가 없었습니다 (시험을 통과한 AI만 봅니다) — 사람이 직접 화면을 확인하세요")
                return

            problems = []
            if double_failed:
                problems.append("The tests passed once and FAILED when run again (flaky or order-dependent). Make them deterministic.")
            if not verification.passed:
                problems.append(f"Tests failed (exit {verification.exit_code}):\n{verification.output_tail[-1500:]}")
            if review.verdict != "PASS":
                problems.append("Reviewer issues:\n- " + "\n- ".join(review.issues or ["(none given)"]))
            if decision and decision.final == "REJECT":
                problems.append("The decision-maker (arbiter) REJECTED this result although tests and reviewers passed. Its reasons: " + decision.reason
                                + ("; conditions: " + "; ".join(decision.conditions) if decision.conditions else ""))
            (run_dir / "last_attempt.patch").write_text(gitops.full_diff(wt, binary=True), encoding="utf-8")
            summary = "last attempt rejected: " + ("tests failed" if not verification.passed else "tests not repeatable" if double_failed else "review FAIL" if review.verdict != "PASS" else "arbiter REJECT")
            if (why := failed(problems, summary)):
                result.status, result.summary = "FAILED", why
                return
