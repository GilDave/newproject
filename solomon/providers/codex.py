"""OpenAI Codex CLI adapter (`codex exec --sandbox <mode> -`, prompt on stdin)."""

from __future__ import annotations

from ..tokens import codex_usage
from .base import CliProvider


class CodexProvider(CliProvider):
    name = "codex"
    cli_name = "codex"

    image_capable = True  # `codex exec -i FILE` (verified per machine by `solomon vision-probe`)

    def build_argv(self, exe: str, write: bool) -> list[str]:
        sandbox = "workspace-write" if write else "read-only"
        imgs = list(getattr(self, "_images", []) or [])
        if imgs:  # `--` ends the multi-value --image list so the trailing "-" (prompt from stdin) is not taken as a file
            return [exe, "exec", "--sandbox", sandbox, *self.extra_args, *[a for i in imgs for a in ("-i", i)], "--", "-"]
        return [exe, "exec", "--sandbox", sandbox, *self.extra_args, "-"]

    def extract_usage(self, stdout: str, stderr: str):
        return codex_usage(stdout, stderr)
