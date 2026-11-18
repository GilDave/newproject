"""OpenAI Codex CLI adapter (`codex exec --sandbox <mode> -`, prompt on stdin)."""

from __future__ import annotations

from .base import CliProvider


class CodexProvider(CliProvider):
    name = "codex"
    cli_name = "codex"

    def build_argv(self, exe: str, write: bool) -> list[str]:
        sandbox = "workspace-write" if write else "read-only"
        return [exe, "exec", "--sandbox", sandbox, *self.extra_args, "-"]
