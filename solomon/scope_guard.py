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


def is_test_path(path: str) -> bool:
    p = normalize_path(path).lower()
    name = p.rsplit("/", 1)[-1]
    return p.startswith(("tests/", "test/")) or "/tests/" in p or "/test/" in p or name.startswith("test_") or name.endswith(("_test.py", ".test.js", ".spec.js", ".test.ts", ".spec.ts"))


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
    # tests are judged on their own: a well-tested small fix must not be rejected because its tests are long
    code_lines = sum(c.added + c.removed for c in changes if not is_test_path(c.path))
    test_lines = sum(c.added + c.removed for c in changes if is_test_path(c.path))
    if code_lines > order.max_diff_lines:
        violations.append(f"{code_lines} diff lines, limit is {order.max_diff_lines}")
    if test_lines > order.max_diff_lines:
        violations.append(f"{test_lines} diff lines in test files, limit is {order.max_diff_lines}")
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
