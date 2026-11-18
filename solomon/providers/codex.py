"""OpenAI Codex CLI adapter (`codex exec --sandbox <mode> -`, prompt on stdin)."""

from __future__ import annotations

from ..tokens import codex_usage
from .base import CliProvider


class CodexProvider(CliProvider):
    name = "codex"
    cli_name = "codex"

    def build_argv(self, exe: str, write: bool) -> list[str]:
        sandbox = "workspace-write" if write else "read-only"
        return [exe, "exec", "--sandbox", sandbox, *self.extra_args, "-"]

    def extract_usage(self, stdout: str, stderr: str):
        return codex_usage(stdout, stderr)
