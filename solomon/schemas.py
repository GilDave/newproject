"""ORDER (the work instruction) and ReviewResult, validated from model JSON."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from typing import Any


def _str_list(data: dict[str, Any], key: str, required: bool = False) -> list[str]:
    value = data.get(key, [])
    if value is None:
        value = []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"'{key}' must be a list of strings")
    if required and not value:
        raise ValueError(f"'{key}' must not be empty")
    return value


def _check_pattern(pattern: str) -> str:
    norm = pattern.strip().replace("\\", "/")
    if not norm or norm in (".", "/", "*", "**", "./"):
        raise ValueError(f"path pattern too broad or empty: {pattern!r}")
    if norm.startswith("/") or norm.startswith("~"):
        raise ValueError(f"path pattern must be project-relative: {pattern!r}")
    if ".." in norm.split("/"):
        raise ValueError(f"path pattern must not contain '..': {pattern!r}")
    return norm


@dataclass
class Order:
    goal: str
    allowed_paths: list[str]
    protected_paths: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    allowed_deletions: list[str] = field(default_factory=list)
    max_files_changed: int = 15
    max_diff_lines: int = 1500
    context: str = ""
    human_steps: list[str] = field(default_factory=list)  # what only the PERSON can do (research with sources, a browser check, approving a plan ...)
    later: list[str] = field(default_factory=list)  # parts of a big goal left for separate, later runs

    @classmethod
    def from_dict(cls, data: dict[str, Any], goal: str, max_files: int, max_lines: int) -> "Order":
        allowed = [_check_pattern(p) for p in _str_list(data, "allowed_paths", required=True)]
        protected = [_check_pattern(p) for p in _str_list(data, "protected_paths")]
        deletions = [_check_pattern(p) for p in _str_list(data, "allowed_deletions")]
        criteria = _str_list(data, "acceptance_criteria")

        def _limit(key: str, ceiling: int) -> int:
            raw = data.get(key, ceiling)
            try:
                value = int(raw)
            except (TypeError, ValueError):
                raise ValueError(f"'{key}' must be an integer") from None
            if value < 1:
                raise ValueError(f"'{key}' must be positive")
            return min(value, ceiling)  # the planner can tighten limits, never loosen them

        return cls(
            goal=goal,
            allowed_paths=allowed,
            protected_paths=protected,
            acceptance_criteria=criteria,
            allowed_deletions=deletions,
            max_files_changed=_limit("max_files_changed", max_files),
            max_diff_lines=_limit("max_diff_lines", max_lines),
            context=str(data.get("context", "")),
            human_steps=[x.strip()[:300] for x in _str_list(data, "human_steps")[:8] if x.strip()],
            later=[x.strip()[:300] for x in _str_list(data, "later")[:12] if x.strip()],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "allowed_paths": self.allowed_paths,
            "protected_paths": self.protected_paths,
            "acceptance_criteria": self.acceptance_criteria,
            "allowed_deletions": self.allowed_deletions,
            "max_files_changed": self.max_files_changed,
            "max_diff_lines": self.max_diff_lines,
            "context": self.context,
            "human_steps": self.human_steps,
            "later": self.later,
        }


@dataclass
class ReviewResult:
    verdict: str  # "PASS" or "FAIL"
    issues: list[str] = field(default_factory=list)
    summary: str = ""
    error: bool = False  # True: the reviewer never gave a verdict (timeout, crash, garbage). Not a real FAIL.
    skipped: bool = False  # True: nobody was asked (the tests had already failed, so the builder must fix those first)
    notes: list[str] = field(default_factory=list)  # minor remarks that do not block: style, wishes, speculative edge cases

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReviewResult":
        verdict = str(data.get("verdict", "")).strip().upper()
        if verdict not in ("PASS", "FAIL"):
            raise ValueError(f"verdict must be PASS or FAIL, got {data.get('verdict')!r}")
        issues = data.get("issues", [])
        if not isinstance(issues, list):
            raise ValueError("'issues' must be a list")
        blockers: list[str] = []
        notes: list[str] = []
        for i in issues:
            if isinstance(i, dict):  # {"severity": "blocker" | "minor", "text": "..."}
                text = str(i.get("text") or i.get("issue") or i.get("description") or "").strip()
                if not text:
                    continue
                (notes if str(i.get("severity", "blocker")).strip().lower() in ("minor", "nit", "suggestion", "low") else blockers).append(text)
            else:
                blockers.append(str(i))  # a plain string is a blocker, as before
        if verdict == "FAIL" and not blockers and notes:
            verdict = "PASS"  # the reviewer found only minor remarks: they are recorded, they do not send the work back
        return cls(verdict=verdict, issues=blockers, summary=str(data.get("summary", "")), notes=notes)


def normalize_path(path: str) -> str:
    return posixpath.normpath(path.replace("\\", "/"))
