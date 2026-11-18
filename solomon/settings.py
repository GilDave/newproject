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
    return {"planner": 300.0, "builder": 900.0, "reviewer": 300.0, "no_output": 180.0, "verify": 600.0}


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
    max_attempts: int = 3
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
        if self.max_attempts < 1:
            raise ConfigError("loop.max_attempts must be >= 1")
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
    s.on_quota = str(loop.get("on_quota", s.on_quota))
    s.quota_wait_sec = float(loop.get("quota_wait_sec", s.quota_wait_sec))
    s.quota_max_waits = int(loop.get("quota_max_waits", s.quota_max_waits))
    s.allow_same_provider = bool(loop.get("allow_same_provider", s.allow_same_provider))
    s.state_dir = str(data.get("state_dir", s.state_dir))
    if data.get("project"):
        s.project_path = str(data["project"])

    s.validate()
    return s
