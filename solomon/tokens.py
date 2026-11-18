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
        cache_create = int(use.get("cache_creation_input_tokens") or 0)
        cache_read = int(use.get("cache_read_input_tokens") or 0)
        tin = int(use.get("input_tokens") or 0) + cache_create + cache_read
        tout = int(use.get("output_tokens") or 0)
    except (ValueError, TypeError, AttributeError):
        return None
    if not (tin or tout):
        return None
    # "in" includes cached input; the split is kept so cache reads can be told apart from fresh input
    result = {"in": tin, "out": tout, "total": tin + tout, "cache_read": cache_read, "cache_create": cache_create}
    turns = envelope.get("num_turns")  # internal model turns of this one call, when the CLI reports it
    if isinstance(turns, int) and turns > 0:
        result["turns"] = turns
    return result


_CODEX_TOTAL = re.compile(r"tokens used[:\s]*([\d,]+)", re.IGNORECASE)


def codex_usage(stdout: str, stderr: str) -> Optional[dict]:
    """Codex prints a 'tokens used' line with a single total."""
    match = _CODEX_TOTAL.search((stdout or "") + "\n" + (stderr or ""))
    if not match:
        return None
    return {"in": None, "out": None, "total": int(match.group(1).replace(",", ""))}


def antigravity_usage(stdout: str) -> Optional[dict]:
    """agy stream-json: the terminal event carries result.usage = {input_tokens, output_tokens, total_tokens,
    cache_read_tokens, ...} and result.num_turns. Older builds (and the ai-dev-director reference) put
    promptTokenCount / candidatesTokenCount in the events instead; that form is the fallback.
    Nothing recognised -> None, so the call is shown as a labelled estimate."""
    for line in reversed((stdout or "").splitlines()):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not (isinstance(event, dict) and event.get("event") == "result" and isinstance(event.get("result"), dict)):
            continue
        result = event["result"]
        use = result.get("usage")
        if isinstance(use, dict):
            try:
                tin, tout = int(use.get("input_tokens") or 0), int(use.get("output_tokens") or 0)
                total = int(use.get("total_tokens") or (tin + tout))
                cached = int(use.get("cache_read_tokens") or 0)
            except (TypeError, ValueError):
                break
            if total:
                out = {"in": tin, "out": tout, "total": total}
                if cached:
                    out["cache_read"] = cached
                if isinstance(result.get("num_turns"), int) and result["num_turns"] > 0:
                    out["turns"] = result["num_turns"]
                return out
        break
    ins = [int(x) for x in re.findall(r'"promptTokenCount"\s*:\s*(\d+)', stdout or "")]
    outs = [int(x) for x in re.findall(r'"candidatesTokenCount"\s*:\s*(\d+)', stdout or "")]
    if not ins and not outs:
        return None
    tin, tout = max(ins or [0]), sum(outs)
    return {"in": tin, "out": tout, "total": tin + tout}
