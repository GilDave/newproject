"""Plan B for subscription limits: remember which AI is out of usage, until when, and let the pipeline route around it.

A limit is never worked around with an API key; the work simply moves to another signed-in AI until the first one resets.
The board is a small file shared by every Solomon process (dashboard jobs, overnight batches), so a limit found by one run is
known to the next one at once.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

DEFAULT_COOLDOWN_MIN = 120.0
GUESS_RETRY_MIN = 20.0  # when the CLI did not say when it resets, the end of the wait is only a guess: try the AI again this often


def parse_reset(detail: str, now: float) -> Optional[float]:
    """The reset time when the CLI says it (older Claude CLIs print 'usage limit reached|<epoch seconds>'), else None."""
    m = re.search(r"\|(\d{10})\b", detail or "")
    if m and float(m.group(1)) > now:
        return float(m.group(1))
    return None


class QuotaBoard:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else None  # None = remembered only inside this process
        self._mem: dict[str, dict] = {}

    @classmethod
    def persistent(cls) -> "QuotaBoard":
        return cls(Path.home() / ".solomon" / "quota.json")

    def _load(self) -> dict:
        if self.path is None:
            return self._mem
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        if self.path is None:
            self._mem = data
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass  # remembering is a convenience; the run itself is never stopped by it

    def mark(self, provider: str, detail: str, now: Optional[float] = None, cooldown_min: float = DEFAULT_COOLDOWN_MIN) -> float:
        now = time.time() if now is None else now
        known = parse_reset(detail, now)
        until = known or now + cooldown_min * 60
        data = self._load()
        data[provider] = {"until": until, "since": now, "detail": (detail or "")[:200], "guess": not known}
        self._save(data)
        return until

    def until(self, provider: str, now: Optional[float] = None) -> float:
        now = time.time() if now is None else now
        e = self._load().get(provider) or {}
        u = float(e.get("until") or 0)
        if e.get("guess") and now - float(e.get("since") or 0) >= GUESS_RETRY_MIN * 60:
            return 0.0  # the usage may have been reset long ago: the next call simply tries this AI again (a new limit marks it again)
        return u if u > now else 0.0

    def exhausted(self, provider: str, now: Optional[float] = None) -> bool:
        return self.until(provider, now) > 0

    def clear(self, provider: Optional[str] = None) -> None:
        data = self._load()
        if provider:
            data.pop(provider, None)
        else:
            data = {}
        self._save(data)

    def snapshot(self, now: Optional[float] = None) -> dict:
        now = time.time() if now is None else now
        return {k: v for k, v in self._load().items() if self.until(k, now) > 0}
