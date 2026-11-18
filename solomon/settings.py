"""Configuration: defaults, optional solomon.toml, and validation."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

ROLES = ("planner", "builder", "reviewer")


class ConfigError(ValueError):
    pass


def _default_roles() -> dict[str, str]:
    # Starting hypothesis, not a fact about model strengths: swap it in solomon.toml.
    return {"planner": "claude", "builder": "codex", "reviewer": "claude"}


def _default_timeouts() -> dict[str, float]:
    return {"planner": 300.0, "builder": 900.0, "reviewer": 300.0, "no_output": 180.0, "verify": 600.0, "analysis": 900.0}


@dataclass
class Settings:
    roles: dict[str, str] = field(default_factory=_default_roles)
    executables: dict[str, Optional[str]] = field(default_factory=dict)
    extra_args: dict[str, list[str]] = field(default_factory=dict)
    timeouts: dict[str, float] = field(default_factory=_default_timeouts)
    max_files_changed: int = 15
    max_diff_lines: int = 1500
    approved_commands: list[str] = field(
        default_factory=lambda: ["python", "python3", "pytest", "npm", "node", "cargo", "go", "make"]
    )
    test_command: Optional[list[str]] = field(default_factory=lambda: ["python3", "-m", "pytest", "-q"])
    max_attempts: int = 3  # 0 = no limit: keep trying until the budgets below or the subscription run out
    max_run_minutes: float = 0.0  # wall-clock budget of one run (0 = none)
    max_tokens: int = 0  # token budget of one run, exact where the CLI reports it (0 = none)
    stagnation_limit: int = 3  # this many IDENTICAL failures in a row = the approach is stuck: restart from clean
    max_restarts: int = 2  # clean restarts allowed before giving up as "no progress"
    double_check_tests: bool = True  # run the tests a second time before declaring a pass (catches flaky passes)
    include_uncommitted: bool = True  # work on the folder as it is on disk (uncommitted edits included), not only on HEAD
    baseline_check: bool = True  # run the tests on the untouched code first: a broken test setup must not burn tokens
    on_quota: str = "stop"  # "stop" ends the run; "wait" sleeps and retries the same call
    quota_wait_sec: float = 900.0
    quota_max_waits: int = 8
    allow_same_provider: bool = False
    state_dir: str = ".solomon"
    project_path: Optional[str] = None  # default target repository (local folder)

    def validate(self) -> None:
        for role in ROLES:
            if not self.roles.get(role):
                raise ConfigError(f"role '{role}' has no provider")
        if self.on_quota not in ("stop", "wait"):
            raise ConfigError("loop.on_quota must be 'stop' or 'wait'")
        if self.max_attempts < 0 or self.max_run_minutes < 0 or self.max_tokens < 0:
            raise ConfigError("loop.max_attempts, max_run_minutes and max_tokens must be >= 0 (0 = no limit)")
        if self.stagnation_limit < 2 or self.max_restarts < 0:
            raise ConfigError("loop.stagnation_limit must be >= 2 and loop.max_restarts >= 0")
        second = self.roles.get("reviewer2")
        if second and second == self.roles.get("builder"):
            raise ConfigError("roles.reviewer2 must not be the builder")
        if self.max_files_changed < 1 or self.max_diff_lines < 1:
            raise ConfigError("safety limits must be positive")


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    return value


def load_settings(path: Optional[str | Path] = None) -> Settings:
    s = Settings()
    cfg_path = Path(path) if path else Path("solomon.toml")
    if not cfg_path.exists():
        if path:
            raise ConfigError(f"config file not found: {cfg_path}")
        s.validate()
        return s
    try:
        data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {cfg_path}: {exc}") from exc

    s.roles.update({k: str(v) for k, v in _section(data, "roles").items()})
    s.executables.update({k: (str(v) or None) for k, v in _section(data, "executables").items()})
    s.extra_args.update({k: [str(a) for a in v] for k, v in _section(data, "extra_args").items()})
    s.timeouts.update({k: float(v) for k, v in _section(data, "timeouts").items()})

    safety = _section(data, "safety")
    s.max_files_changed = int(safety.get("max_files_changed", s.max_files_changed))
    s.max_diff_lines = int(safety.get("max_diff_lines", s.max_diff_lines))
    if "approved_commands" in safety:
        s.approved_commands = [str(c) for c in safety["approved_commands"]]

    verification = _section(data, "verification")
    if "test_command" in verification:
        cmd = [str(c) for c in verification["test_command"]]
        s.test_command = cmd or None

    loop = _section(data, "loop")
    s.max_attempts = int(loop.get("max_attempts", s.max_attempts))
    s.max_run_minutes = float(loop.get("max_run_minutes", s.max_run_minutes))
    s.max_tokens = int(loop.get("max_tokens", s.max_tokens))
    s.stagnation_limit = int(loop.get("stagnation_limit", s.stagnation_limit))
    s.max_restarts = int(loop.get("max_restarts", s.max_restarts))
    s.double_check_tests = bool(loop.get("double_check_tests", s.double_check_tests))
    s.baseline_check = bool(loop.get("baseline_check", s.baseline_check))
    s.include_uncommitted = bool(loop.get("include_uncommitted", s.include_uncommitted))
    s.on_quota = str(loop.get("on_quota", s.on_quota))
    s.quota_wait_sec = float(loop.get("quota_wait_sec", s.quota_wait_sec))
    s.quota_max_waits = int(loop.get("quota_max_waits", s.quota_max_waits))
    s.allow_same_provider = bool(loop.get("allow_same_provider", s.allow_same_provider))
    s.state_dir = str(data.get("state_dir", s.state_dir))
    if data.get("project"):
        s.project_path = str(data["project"])

    s.validate()
    return s
