"""Git helpers: isolated worktrees, change collection, patch apply."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .scope_guard import Change


class GitError(RuntimeError):
    pass


def git(args: list[str], cwd: str | Path, check: bool = True, timeout: float = 120.0) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc


def ensure_repo(project: Path) -> None:
    if not project.is_dir():
        raise GitError(f"project folder does not exist: {project}")
    if git(["rev-parse", "--is-inside-work-tree"], project, check=False).returncode != 0:
        raise GitError(f"not a git repository: {project}")
    if git(["rev-parse", "--verify", "HEAD"], project, check=False).returncode != 0:
        raise GitError(f"repository has no commits yet: {project} (make an initial commit first)")


def is_dirty(project: Path) -> bool:
    return bool(git(["status", "--porcelain"], project).stdout.strip())


def exclude_from_git(project: Path, name: str) -> None:
    """Keep the state folder out of `git status` without touching .gitignore."""
    raw = git(["rev-parse", "--git-path", "info/exclude"], project).stdout.strip()
    path = Path(raw) if Path(raw).is_absolute() else project / raw
    path.parent.mkdir(parents=True, exist_ok=True)
    line = name.rstrip("/") + "/"
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if line not in existing:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(("\n" if existing and existing[-1] else "") + line + "\n")


def create_worktree(project: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    git(["worktree", "add", "--detach", str(dest), "HEAD"], project)


def remove_worktree(project: Path, dest: Path) -> None:
    git(["worktree", "remove", "--force", str(dest)], project, check=False)
    git(["worktree", "prune"], project, check=False)


def reset_worktree(wt: Path) -> None:
    git(["reset", "--hard", "HEAD"], wt)
    git(["clean", "-fd"], wt)


def clean_untracked(wt: Path) -> None:
    """Drop files created by the test run (e.g. __pycache__). collect_changes() staged the
    builder's work first, so only verification leftovers are still untracked here."""
    git(["clean", "-fdq"], wt, check=False)


def list_files(wt: Path, limit: int = 300) -> list[str]:
    files = git(["ls-files"], wt).stdout.splitlines()
    return files[:limit]


def collect_changes(wt: Path) -> list[Change]:
    git(["add", "-A"], wt)
    status = git(["diff", "--cached", "--name-status", "--no-renames"], wt).stdout.splitlines()
    numstat = git(["diff", "--cached", "--numstat", "--no-renames"], wt).stdout.splitlines()
    counts: dict[str, tuple[int, int]] = {}
    for line in numstat:
        parts = line.split("\t", 2)
        if len(parts) == 3:
            added = int(parts[0]) if parts[0].isdigit() else 0  # '-' marks a binary file
            removed = int(parts[1]) if parts[1].isdigit() else 0
            counts[parts[2]] = (added, removed)
    changes = []
    for line in status:
        code, _, path = line.partition("\t")
        added, removed = counts.get(path, (0, 0))
        changes.append(Change(path=path, status=code[:1], added=added, removed=removed))
    return changes


def full_diff(wt: Path, binary: bool = False) -> str:
    args = ["diff", "--cached", "--no-renames"] + (["--binary"] if binary else [])
    return git(args, wt).stdout


def apply_patch(project: Path, patch_file: Path) -> None:
    git(["apply", "--check", str(patch_file)], project)
    git(["apply", str(patch_file)], project)
