"""Token accounting. Exact numbers only when the CLI reports them; otherwise an estimate."""

from __future__ import annotations

import json
import re
from typing import Optional


def estimate_tokens(text: Optional[str]) -> int:
    """Rough estimate: ~4 chars/token for ASCII (code, English), ~1.5 for Hangul and other scripts."""
    if not text:
        return 0
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    return int(ascii_chars / 4 + (len(text) - ascii_chars) / 1.5) + 1


def claude_usage(stdout: str) -> Optional[dict]:
    """Claude Code `--output-format json` envelope: {"usage": {...}}."""
    try:
        envelope = json.loads(stdout)
        use = envelope.get("usage") or {}
        tin = sum(int(use.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        tout = int(use.get("output_tokens") or 0)
    except (ValueError, TypeError, AttributeError):
        return None
    if not (tin or tout):
        return None
    return {"in": tin, "out": tout, "total": tin + tout}


_CODEX_TOTAL = re.compile(r"tokens used[:\s]*([\d,]+)", re.IGNORECASE)


def codex_usage(stdout: str, stderr: str) -> Optional[dict]:
    """Codex prints a 'tokens used' line with a single total."""
    match = _CODEX_TOTAL.search((stdout or "") + "\n" + (stderr or ""))
    if not match:
        return None
    return {"in": None, "out": None, "total": int(match.group(1).replace(",", ""))}
