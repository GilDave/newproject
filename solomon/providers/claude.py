"""Claude Code CLI adapter (`claude --print --output-format json`, prompt on stdin)."""

from __future__ import annotations

from ..tokens import claude_usage
from .base import CliProvider, ProviderError, QuotaExhausted, looks_like_quota, parse_json_envelope


class ClaudeProvider(CliProvider):
    name = "claude"
    cli_name = "claude"

    def build_argv(self, exe: str, write: bool) -> list[str]:
        argv = [exe, "--print", "--output-format", "json"]
        if write:
            argv += ["--permission-mode", "acceptEdits"]
        return argv + self.extra_args

    def parse_output(self, stdout: str) -> str:
        envelope = parse_json_envelope(stdout)
        if envelope is None:
            return stdout
        result = str(envelope.get("result", ""))
        if envelope.get("is_error"):
            if looks_like_quota(result):
                raise QuotaExhausted(f"claude: {result[:500]}")
            raise ProviderError(f"claude reported an error: {result[:500]}")
        return result

    def extract_usage(self, stdout: str, stderr: str):
        return claude_usage(stdout)
