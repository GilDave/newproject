"""The pipeline: PLAN -> BUILD -> scope check -> VERIFY -> cross REVIEW -> decide.

The final decision is deterministic in the MVP: PASS only when the scope guard is
clean, the tests pass and the reviewer (a different provider than the builder)
says PASS. A reviewer that fails or returns garbage counts as FAIL (fail-closed).
"""

from __future__ import annotations

import json
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
    reduced_independence: bool = False
    warnings: list[str] = field(default_factory=list)


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        providers: Optional[dict[str, Provider]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.s = settings
        self.providers = providers if providers is not None else build_providers(settings)
        self._sleep = sleep
        for role in ROLES:
            name = settings.roles[role]
            if name not in self.providers:
                raise ConfigError(f"role '{role}' uses unknown provider '{name}' (known: {sorted(self.providers)})")
        if not getattr(self.providers[settings.roles["builder"]], "supports_write", True):
            raise ConfigError(
                f"'{settings.roles['builder']}' cannot be the builder (it is limited to read-only roles: planner, reviewer)"
            )
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
                    timeout=self.s.timeouts[role],
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
    def _review(self, order: Order, wt: Path, verification: VerifyResult, warnings: list[str], log: RunLog) -> ReviewResult:
        summary = (
            "tests skipped (UNVERIFIED)"
            if verification.skipped
            else f"command: {verification.command}\nexit code: {verification.exit_code}\n{verification.output_tail[-1500:]}"
        )
        prompt = prompts.reviewer(order, gitops.full_diff(wt), summary, warnings)
        try:
            res = self._call("reviewer", prompt, wt, False, log)
        except BlockedByQuota:
            raise
        except ProviderError as exc:
            return ReviewResult("FAIL", [f"reviewer unavailable, treated as FAIL: {exc}"], error=True)
        try:
            return ReviewResult.from_dict(extract_json(res.text))
        except ValueError as exc:
            return ReviewResult("FAIL", [f"unparseable review, treated as FAIL: {exc}"], error=True)

    # -------------------------------------------------------------------- run
    def run(self, goal: str, project: str | Path, keep_worktree: bool = False) -> RunResult:
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
            gitops.create_worktree(project, wt)
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

    def _build_loop(self, order: Order, wt: Path, run_dir: Path, result: RunResult, log: RunLog) -> None:
        feedback: Optional[str] = None
        reset_next = False
        for attempt in range(1, self.s.max_attempts + 1):
            result.attempts = attempt
            if reset_next:
                gitops.reset_worktree(wt)
                reset_next = False
            log.event("attempt_start", attempt=attempt)

            try:
                self._call("builder", prompts.builder(order, feedback), wt, True, log)
            except ProviderError as exc:
                feedback, reset_next = f"The builder CLI failed: {exc}", True
                result.summary = feedback
                continue

            changes = gitops.collect_changes(wt)
            if not changes:
                feedback, reset_next = "No files were changed. Implement the ORDER by editing files.", False
                result.summary = "builder made no changes"
                continue
            log.event("changes", count=len(changes), files=[
                {"path": c.path, "status": c.status, "added": c.added, "removed": c.removed} for c in changes[:40]])
            violations = scope_guard.check(order, changes)
            if violations:
                log.event("scope_violation", violations=violations)
                feedback = "Scope violations (these changes were discarded):\n- " + "\n- ".join(violations)
                reset_next = True
                result.summary = "scope violation: " + "; ".join(violations)
                continue

            verification = verify(self.s, wt)
            gitops.clean_untracked(wt)
            log.event("verify", passed=verification.passed, skipped=verification.skipped, exit_code=verification.exit_code,
                      command=verification.command, output_tail=verification.output_tail[-1500:])
            warnings = ["tests were not run: result is UNVERIFIED"] if verification.skipped else []
            review = self._review(order, wt, verification, warnings, log)
            retries = 0
            while review.error and retries < REVIEW_RETRIES:
                # No verdict is not a verdict. Ask the reviewer again about the SAME code instead of paying the
                # builder to redo work that nobody found fault with.
                retries += 1
                log.event("review_retry", attempt=attempt, retry=retries, reason=review.issues[0] if review.issues else "")
                review = self._review(order, wt, verification, warnings, log)
            if review.error:
                (run_dir / "last_attempt.patch").write_text(gitops.full_diff(wt, binary=True), encoding="utf-8")
                reviewer = self.s.roles["reviewer"]
                result.status = "ERROR"
                result.summary = (f"검수 AI({reviewer})가 {1 + REVIEW_RETRIES}번 모두 판정을 내지 못해 중단했습니다. "
                                  f"구현은 다시 시키지 않았습니다 (마지막 코드는 last_attempt.patch). 원인: {review.issues[0] if review.issues else '?'}")
                log.event("review", verdict="ERROR", issues=review.issues)
                return
            log.event("review", verdict=review.verdict, issues=review.issues)

            if verification.passed and review.verdict == "PASS":
                patch = run_dir / "result.patch"
                patch.write_text(gitops.full_diff(wt, binary=True), encoding="utf-8")
                result.status, result.patch_path = "PASSED", str(patch)
                result.summary = review.summary or "passed scope check, tests and independent review"
                if verification.skipped:
                    result.warnings.append("no test command configured: passed without automatic tests")
                return

            problems = []
            if not verification.passed:
                problems.append(f"Tests failed (exit {verification.exit_code}):\n{verification.output_tail[-1500:]}")
            if review.verdict != "PASS":
                problems.append("Reviewer issues:\n- " + "\n- ".join(review.issues or ["(none given)"]))
            feedback = "\n\n".join(problems)
            result.summary = "last attempt rejected: " + ("tests failed" if not verification.passed else "review FAIL")
            (run_dir / "last_attempt.patch").write_text(gitops.full_diff(wt, binary=True), encoding="utf-8")

        result.status = "FAILED"
        result.summary = f"no attempt passed after {self.s.max_attempts} tries; {result.summary}"
