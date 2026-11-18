"""Google Gemini CLI adapter (`gemini`, signed in with a Google account).

The flags below are the documented non-interactive form as best known; they were NOT run against
a real Gemini CLI yet. Confirm with `gemini --help` and adjust via solomon.toml:
    [extra_args]
    gemini = ["-m", "gemini-2.5-pro"]
Billing safety: GEMINI_API_KEY / GOOGLE_API_KEY / Vertex switches are removed from the environment
before every call (see env.py), so only the account login can be used.
"""

from __future__ import annotations

from ..tokens import gemini_usage
from .base import CliProvider, ProviderError, QuotaExhausted, looks_like_quota, parse_json_envelope


class GeminiProvider(CliProvider):
    name = "gemini"
    cli_name = "gemini"

    def build_argv(self, exe: str, write: bool) -> list[str]:
        # The real prompt arrives on stdin; -p switches to non-interactive mode.
        argv = [exe, "--output-format", "json", "-p", "Follow the instructions given in the input."]
        argv += ["--approval-mode", "auto_edit"] if write else []  # read-only calls never auto-approve edits
        return argv + self.extra_args

    def parse_output(self, stdout: str) -> str:
        envelope = parse_json_envelope(stdout)
        if envelope is None:
            return stdout
        error = envelope.get("error")
        if error:
            text = str(error.get("message", error) if isinstance(error, dict) else error)
            if looks_like_quota(text):
                raise QuotaExhausted(f"gemini: {text[:500]}")
            raise ProviderError(f"gemini reported an error: {text[:500]}")
        response = envelope.get("response")
        return response if isinstance(response, str) else stdout

    def extract_usage(self, stdout: str, stderr: str):
        return gemini_usage(stdout)
