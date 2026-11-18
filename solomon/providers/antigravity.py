"""Google Antigravity CLI adapter (`agy`), Google's subscription route after Gemini CLI's personal sign-in ended.

Modelled on the invocation the ai-dev-director reference documents and uses daily:
  agy --input-format stream-json --output-format stream-json --sandbox
  stdin : one NDJSON line {"event":"user","message":{"role":"user","content":[{"type":"text","text":...}]}}
  stdout: NDJSON events; the last {"event":"result","result":{"status":"SUCCESS","response":...}} is the answer.
Checked against a real `agy` (help text and one live call): the flags exist and the result event has this shape.

Read-only roles only (planner, reviewer): the call runs in an EMPTY temporary folder with tools told off,
so it can only judge what the prompt contains. A BUILDER needs enforced write scoping (the reference installs
permission rules and hooks for that); until that exists here, Solomon refuses to start with agy as builder.
"""

from __future__ import annotations

import json
import tempfile
from typing import Optional

from ..tokens import antigravity_usage
from .base import (
    CliProvider,
    ProviderError,
    ProviderResult,
    QuotaExhausted,
    looks_like_quota,
)

NO_TOOLS = "Do not use tools. Use only the evidence supplied in this message.\n"

# `agy models` on a signed-in account also lists Claude and GPT-OSS models, and "auto" may pick any of them.
# A Claude model behind agy would review Claude's own work and silently void the cross-review, so a Google
# model is pinned unless the config names one (extra_args.antigravity = ["--model", "<id from `agy models`>"]).
DEFAULT_MODEL = "gemini-3.8-flash-medium"  # "high" thought for minutes in practice and hit the silence limit

# agy can think silently for minutes before its first event, so it is never killed for silence, and its hard
# limit is at least this long whatever the role's default is.
MIN_HARD_TIMEOUT = 600.0


class TransientStreamError(ProviderError):
    """The stream ended without a usable result; one fresh retry is allowed."""


class AntigravityProvider(CliProvider):
    name = "antigravity"
    cli_name = "agy"
    supports_write = False

    def build_argv(self, exe: str, write: bool) -> list[str]:
        argv = [exe, "--input-format", "stream-json", "--output-format", "stream-json", "--sandbox"]
        if "--model" not in self.extra_args:
            argv += ["--model", DEFAULT_MODEL]
        return argv + self.extra_args

    def prepare_stdin(self, prompt: str) -> str:
        message = {"event": "user", "message": {"role": "user", "content": [{"type": "text", "text": NO_TOOLS + prompt}]}}
        return json.dumps(message, ensure_ascii=False) + "\n"

    def parse_output(self, stdout: str) -> str:
        final: Optional[dict] = None
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue  # progress noise or a partial line
            if isinstance(event, dict) and event.get("event") == "result" and isinstance(event.get("result"), dict):
                final = event["result"]
        if final is None:
            raise TransientStreamError("antigravity: the stream ended without a terminal result")
        if final.get("status") != "SUCCESS":
            detail = str(final.get("error") or final.get("status") or "UNKNOWN")[:500]
            if looks_like_quota(detail):
                raise QuotaExhausted(f"antigravity: {detail}")
            raise ProviderError(f"antigravity: the call failed: {detail}")
        response = str(final.get("response") or "")
        if not response.strip():
            raise TransientStreamError("antigravity: SUCCESS with an empty response")
        return response

    def extract_usage(self, stdout: str, stderr: str):
        return antigravity_usage(stdout)

    def run(self, prompt, cwd, write, timeout, no_output_timeout=None) -> ProviderResult:
        if write:
            raise ProviderError("antigravity cannot be a builder yet: enforced write scoping is not implemented")
        with tempfile.TemporaryDirectory(prefix="solomon-agy-") as empty:
            for attempt in (1, 2):
                try:
                    return super().run(prompt, cwd=empty, write=False, timeout=max(timeout, MIN_HARD_TIMEOUT), no_output_timeout=None)
                except TransientStreamError:
                    if attempt == 2:
                        raise
        raise AssertionError("unreachable")  # pragma: no cover
