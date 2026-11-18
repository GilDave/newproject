"""Environment helpers: strip pay-per-use API keys and locate the signed-in CLIs."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

# Any of these in the environment could make a CLI bill per token instead of
# using the subscription login, so they are removed before every CLI call.
FORBIDDEN_API_KEYS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_GENAI_USE_VERTEXAI",  # switches Gemini CLI to billed Vertex AI
    "AZURE_OPENAI_API_KEY",
)


def sanitized_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in FORBIDDEN_API_KEYS:
        env.pop(key, None)
    return env


def present_api_keys() -> list[str]:
    """Names of forbidden keys currently set (reported by `solomon doctor`)."""
    return [k for k in FORBIDDEN_API_KEYS if os.environ.get(k)]


def _posix_candidates(name: str) -> list[Path]:
    home = Path.home()
    dirs = [
        home / ".local" / "bin",
        home / ".npm-global" / "bin",
        home / ".claude" / "local",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    ]
    return [d / name for d in dirs]


def resolve_cli(name: str) -> Optional[str]:
    """Find an installed CLI on PATH or in the usual macOS/Linux install folders."""
    found = shutil.which(name)
    if found:
        return str(Path(found).resolve())
    for cand in _posix_candidates(name):
        try:
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand.resolve())
        except OSError:
            continue
    return None
