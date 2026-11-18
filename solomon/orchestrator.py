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

from . import context, gitops, prompts, scope_guard
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


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        providers: Optional[dict[str, Provider]] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ):
        self.s = settings
        self.providers = providers if providers is not None else build_providers(settings)
        self._sleep = sleep
        self._clock = clock
        self._tokens = 0
        for role in ROLES:
            name = settings.roles[role]
            if name not in self.providers:
                raise ConfigError(f"role '{role}' uses unknown provider '{name}' (known: {sorted(self.providers)})")
        if not getattr(self.providers[settings.roles["builder"]], "supports_write", True):
            raise ConfigError(
                f"'{settings.roles['builder']}' cannot be the builder (it is limited to read-only roles: planner, reviewer)"
            )
        second = settings.roles.get("reviewer2")
        if second:
            if second not in self.providers:
                raise ConfigError(f"role 'reviewer2' uses unknown provider '{second}' (known: {sorted(self.providers)})")
            if second == settings.roles["builder"]:
                raise ConfigError("the second reviewer must not be the builder")
        self.reduced_independence = settings.roles["builder"] == settings.roles["reviewer"]
        if self.reduced_independence and not settings.allow_same_provider:
            raise ConfigError(
                "builder and reviewer must use different providers "
                "(set loop.allow_same_provider = true to accept REDUCED_INDEPENDENCE)"
            )

    # ------------------------------------------------------------------ calls
    def _call(self, role: str, prompt: str, cwd: Path, write: bool, log: RunLog) -> ProviderResult:
        provider = self.providers[self.s.roles[role]]
        waits = 0
        while True:
            log.event("call_start", role=role, provider=provider.name, write=write, prompt_chars=len(prompt))
            try:
                res = provider.run(
                    prompt,
                    cwd=str(cwd),
                    write=write,
                    timeout=self.s.timeouts.get(role, self.s.timeouts["reviewer"]),
                    no_output_timeout=self.s.timeouts["no_output"],
                )
            except QuotaExhausted as exc:
                log.event("quota", role=role, provider=provider.name, detail=str(exc), waits=waits)
                if self.s.on_quota == "wait" and waits < self.s.quota_max_waits:
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

    # ------------------------------------------------------------------- plan
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
            log.event("order", order=order.to_dict())
            return order
        raise PlanError(f"planner did not produce a valid ORDER: {error}")

    # ----------------------------------------------------------------- review
    def _review_role(self, role: str, order: Order, wt: Path, verification: VerifyResult, warnings: list[str],
                     log: RunLog, attempt: int) -> ReviewResult:
        """One reviewer's verdict. No verdict (timeout, crash, garbage) is asked again about the SAME code."""
        summary = (
            "tests skipped (UNVERIFIED)"
            if verification.skipped
            else f"command: {verification.command}\nexit code: {verification.exit_code}\n{verification.output_tail[-1500:]}"
        )
        prompt = prompts.reviewer(order, gitops.full_diff(wt), summary, warnings)
        last = ReviewResult("FAIL", ["no verdict"], error=True)
        for retry in range(1 + REVIEW_RETRIES):
            if retry:
                log.event("review_retry", attempt=attempt, role=role, retry=retry, reason=last.issues[0] if last.issues else "")
            try:
                res = self._call(role, prompt, wt, False, log)
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
                attempt: int = 0) -> ReviewResult:
        """Cross-check: the reviewer, and the second reviewer when configured. Every reviewer must say PASS.
        The second one is only asked about code the first already accepted (time is money)."""
        agg = ReviewResult("PASS", [], "")
        for role in [r for r in ("reviewer", "reviewer2") if self.s.roles.get(r)]:
            who = self.s.roles[role]
            one = self._review_role(role, order, wt, verification, warnings, log, attempt)
            log.event("review_role", role=role, provider=who, verdict=one.verdict, error=one.error, issues=one.issues)
            if one.error:
                return ReviewResult("FAIL", [f"[{who}] {i}" for i in one.issues], error=True)
            if role == "reviewer":
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
        if gitops.is_dirty(project):
            result.warnings.append("project has uncommitted changes; they are NOT part of the isolated worktree")
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
                nonlocal feedback, reset_next, restarts
                result.summary = summary
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
                    feedback += ("\n\nTHE SAME FAILURE REPEATED " + str(s.stagnation_limit) + " TIMES. The code was reset to its "
                                 "original state. Take a COMPLETELY DIFFERENT approach. Attempts so far:\n" + "\n".join(history[-6:]))
                elif len(history) > 1:
                    feedback += "\n\nEarlier attempts (do not repeat them):\n" + "\n".join(history[-6:-1])
                return None

            try:
                self._call("builder", prompts.builder(order, feedback), wt, True, log)
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
            review = self._review(order, wt, verification, warnings, log, attempt)
            if review.error:
                (run_dir / "last_attempt.patch").write_text(gitops.full_diff(wt, binary=True), encoding="utf-8")
                result.status = "ERROR"
                result.summary = (f"검수 AI가 {1 + REVIEW_RETRIES}번 모두 판정을 내지 못해 중단했습니다. 구현은 다시 시키지 않았습니다 "
                                  f"(마지막 코드는 last_attempt.patch). 원인: {review.issues[0] if review.issues else '?'}")
                log.event("review", verdict="ERROR", issues=review.issues)
                return
            log.event("review", verdict=review.verdict, issues=review.issues)

            double_failed = False
            if verification.passed and review.verdict == "PASS" and s.double_check_tests and not verification.skipped:
                again = verify(s, wt)  # double check: a pass that does not repeat is not a pass
                gitops.clean_untracked(wt)
                log.event("double_check", passed=again.passed, exit_code=again.exit_code, output_tail=again.output_tail[-800:])
                double_failed = not again.passed

            if verification.passed and review.verdict == "PASS" and not double_failed:
                patch = run_dir / "result.patch"
                patch.write_text(gitops.full_diff(wt, binary=True), encoding="utf-8")
                if getattr(self, "_on_pass", None):
                    result.commit = self._on_pass(wt, result)
                result.status, result.patch_path = "PASSED", str(patch)
                result.summary = review.summary or "passed scope check, tests and independent review"
                if verification.skipped:
                    result.warnings.append("no test command configured: passed without automatic tests")
                return

            problems = []
            if double_failed:
                problems.append("The tests passed once and FAILED when run again (flaky or order-dependent). Make them deterministic.")
            if not verification.passed:
                problems.append(f"Tests failed (exit {verification.exit_code}):\n{verification.output_tail[-1500:]}")
            if review.verdict != "PASS":
                problems.append("Reviewer issues:\n- " + "\n- ".join(review.issues or ["(none given)"]))
            (run_dir / "last_attempt.patch").write_text(gitops.full_diff(wt, binary=True), encoding="utf-8")
            summary = "last attempt rejected: " + ("tests failed" if not verification.passed else "tests not repeatable" if double_failed else "review FAIL")
            if (why := failed(problems, summary)):
                result.status, result.summary = "FAILED", why
                return
