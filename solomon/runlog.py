"""Append-only JSONL log of every step of a run."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class RunLog:
    def __init__(self, path: Path):
        self.path = path

    def event(self, kind: str, **fields: Any) -> None:
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": kind, **fields}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
