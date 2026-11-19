"""Provider interface. One adapter per model vendor; roles are assigned in config.

Adding Gemini later = one new CliProvider subclass + one line in providers/__init__.py.
"""

from __future__ import annotations

import abc
import json
from dataclasses import dataclass
from typing import Optional

from ..env import resolve_cli, sanitized_env
from ..runner import run_process


class ProviderError(Exception):
    """A provider call failed (non-zero exit, unusable output)."""


class ProviderUnavailable(ProviderError):
    """The CLI is not installed."""


class ProviderTimeout(ProviderError):
    pass


class QuotaExhausted(ProviderError):
    """The subscription's usage limit was hit. Never worked around with an API key."""


@dataclass
class ProviderResult:
    text: str
    exit_code: int
    duration_sec: float
    provider: str
    tokens: Optional[dict] = None  # exact counts reported by the CLI, when it reports them


# Phrases are matched case-insensitively against CLI output. Matching text is
# fragile, so it is only consulted AFTER the exit code or an error flag already
# says the call failed.
_QUOTA_PHRASES = (
    "usage limit",
    "rate limit",
    "quota",
    "subscription limit",
    "limit reached",
    "too many requests",
    "resource_exhausted",
    "exhausted your",
    "429",
    "resets at",
    "try again later",
)


def looks_like_quota(text: str) -> bool:
    low = (text or "").lower()
    return any(p in low for p in _QUOTA_PHRASES)


class Provider(abc.ABC):
    name: str
    supports_write = True  # False for providers limited to read-only roles (planner, reviewer)

    @abc.abstractmethod
    def run(
        self,
        prompt: str,
        cwd: str,
        write: bool,
        timeout: float,
        no_output_timeout: Optional[float] = None,
        images: Optional[list[str]] = None,
    ) -> ProviderResult: ...


class CliProvider(Provider):
    cli_name: str

    def __init__(self, executable: Optional[str] = None, extra_args: Optional[list[str]] = None):
        self.executable = executable
        self.extra_args = list(extra_args or [])

    @abc.abstractmethod
    def build_argv(self, exe: str, write: bool) -> list[str]: ...

    def parse_output(self, stdout: str) -> str:
        return stdout

    def prepare_stdin(self, prompt: str) -> str:
        """What is written to the CLI's stdin. Most CLIs take the prompt as is."""
        return prompt

    def extract_usage(self, stdout: str, stderr: str) -> Optional[dict]:
        """Token counts the CLI reported (exact), or None. Subclasses override."""
        return None

    def locate(self) -> Optional[str]:
        return self.executable or resolve_cli(self.cli_name)

    def run(
        self,
        prompt: str,
        cwd: str,
        write: bool,
        timeout: float,
        no_output_timeout: Optional[float] = None,
        images: Optional[list[str]] = None,
    ) -> ProviderResult:
        exe = self.locate()
        if not exe:
            raise ProviderUnavailable(f"'{self.cli_name}' CLI not found. Install it and sign in with your subscription.")
        if images and not getattr(self, "image_capable", False):
            raise ProviderError(f"{self.name}: 이미지 입력을 지원하지 않습니다")
        self._images = list(images or [])  # read by build_argv of image-capable CLIs (reviews run one at a time)
        argv = self.build_argv(exe, write)
        try:
            res = run_process(
                argv,
                cwd=cwd,
                env=sanitized_env(),
                timeout=timeout,
                no_output_timeout=no_output_timeout,
                stdin_data=self.prepare_stdin(prompt),
            )
        except OSError as exc:
            raise ProviderUnavailable(f"could not start {self.cli_name}: {exc}") from exc
        if res.timed_out:
            raise ProviderTimeout(f"{self.name}: {res.timeout_reason}")
        if res.exit_code != 0:
            detail = (res.stderr.strip() or res.stdout.strip())[:500]
            if looks_like_quota(detail):
                raise QuotaExhausted(f"{self.name}: {detail}")
            raise ProviderError(f"{self.name} exited with {res.exit_code}: {detail}")
        try:
            tokens = self.extract_usage(res.stdout, res.stderr)
        except Exception:  # accounting must never break a call
            tokens = None
        return ProviderResult(
            text=self.parse_output(res.stdout),
            exit_code=res.exit_code,
            duration_sec=res.duration_sec,
            provider=self.name,
            tokens=tokens,
        )


def parse_json_envelope(stdout: str) -> Optional[dict]:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
