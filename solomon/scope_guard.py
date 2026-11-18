"""Scope Guard: reject patches that leave the ORDER's boundaries.

The builder cannot loosen these checks; they run on the git diff, not on what the
builder claims it changed.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from .schemas import Order, normalize_path


@dataclass
class Change:
    path: str
    status: str  # A, M, D
    added: int = 0
    removed: int = 0


def path_matches(path: str, pattern: str) -> bool:
    path = normalize_path(path)
    pattern = pattern.replace("\\", "/")
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatch(path, pattern)
    base = pattern.rstrip("/")
    return path == base or path.startswith(base + "/")


def check(order: Order, changes: list[Change]) -> list[str]:
    violations: list[str] = []
    if len(changes) > order.max_files_changed:
        violations.append(f"{len(changes)} files changed, limit is {order.max_files_changed}")
    total_lines = sum(c.added + c.removed for c in changes)
    if total_lines > order.max_diff_lines:
        violations.append(f"{total_lines} diff lines, limit is {order.max_diff_lines}")
    for c in changes:
        if any(path_matches(c.path, p) for p in order.protected_paths):
            violations.append(f"{c.path}: protected path was modified")
            continue
        if not any(path_matches(c.path, p) for p in order.allowed_paths):
            violations.append(f"{c.path}: outside allowed_paths")
            continue
        if c.status == "D" and not any(path_matches(c.path, p) for p in order.allowed_deletions):
            violations.append(f"{c.path}: deletion is not allowed by the ORDER")
    return violations
