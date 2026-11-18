"""Give the planner the content of the files the goal points at (stack traces, file names).

The planner runs without tools, so without this it only sees file NAMES and has to guess
which files to allow. Reading what the goal names makes the ORDER's allowed_paths realistic.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_MAX_FILE_BYTES = 200_000


def _find(goal: str, needle: str) -> int:
    """Position of `needle` as a whole name: 'a.py' must not match inside 'data.py'."""
    m = re.search(r"(?<![\w.\-])" + re.escape(needle) + r"(?!\w)", goal)
    return m.start() if m else -1


def referenced_files(
    goal: str, files: list[str], root: Path, max_files: int = 5, max_lines: int = 300, max_chars: int = 30_000
) -> str:
    hits: list[tuple[int, str]] = []
    for f in files:
        pos = _find(goal, f)
        if pos < 0:
            base = os.path.basename(f)
            if len(base) >= 4:  # shorter names match too much text by accident
                pos = _find(goal, base)
        if pos >= 0:
            hits.append((pos, f))
    hits.sort()
    parts: list[str] = []
    used = 0
    for _, rel in hits[:max_files]:
        path = root / rel
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue  # binary or unreadable: skip silently
        shown = lines[:max_lines]
        note = f"\n... [{len(lines) - max_lines} more lines not shown]" if len(lines) > max_lines else ""
        chunk = f"--- {rel} ---\n" + "\n".join(shown) + note
        if used + len(chunk) > max_chars:
            break
        parts.append(chunk)
        used += len(chunk)
    return "\n\n".join(parts)
