"""Claude Code CLI adapter (`claude --print --output-format json`, prompt on stdin)."""

from __future__ import annotations

from ..tokens import claude_usage
from .base import CliProvider, ProviderError, ProviderResult, QuotaExhausted, looks_like_quota, parse_json_envelope


# Planner and reviewer get everything they need inside the prompt, so read-only calls run without
# tools (no file-hunting turns, smaller context) and without any MCP servers. NEVER add --bare:
# it skips the subscription (OAuth/keychain) login and would bill per token.
DEFAULT_READONLY_ARGS = ["--tools", "", "--strict-mcp-config"]

# `--output-format json` prints NOTHING until the whole answer is ready, and a builder that reads, edits and tests files easily
# stays silent for many minutes. A silence limit would kill healthy work (that is exactly what NO_OUTPUT_TIMEOUT did), so Claude is
# never killed for silence; only the hard limit applies, and a write call gets at least this long.
MIN_WRITE_TIMEOUT = 1200.0


class ClaudeProvider(CliProvider):
    name = "claude"
    cli_name = "claude"

    def __init__(self, executable=None, extra_args=None, readonly_args=None, write_args=None):
        super().__init__(executable=executable, extra_args=extra_args)
        self.readonly_args = list(DEFAULT_READONLY_ARGS if readonly_args is None else readonly_args)
        # Extra arguments for write (builder) calls, e.g. ["--tools", "Read,Edit,Write,Glob,Grep"] to keep
        # the builder to file tools. Off by default until its saving is measured on your machine.
        self.write_args = list(write_args or [])

    def build_argv(self, exe: str, write: bool) -> list[str]:
        argv = [exe, "--print", "--output-format", "json"]
        argv += ["--permission-mode", "acceptEdits", *self.write_args] if write else self.readonly_args
        return argv + self.extra_args

    def run(self, prompt, cwd, write, timeout, no_output_timeout=None) -> ProviderResult:
        return super().run(prompt, cwd, write, max(timeout, MIN_WRITE_TIMEOUT) if write else timeout, no_output_timeout=None)

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
