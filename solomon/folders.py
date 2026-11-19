"""Folder operations the dashboard does for you, so the terminal is not needed: browse, rename, git init, open in Finder.

Everything is limited on purpose: browsing stays inside the user's home folder, renaming is only for projects Solomon
created, `git init` never commits secret files, and "open" only opens things that belong to a registered project."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import gitops
from .projects import MARKER, NAME_RE, ProjectError, is_managed

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".idea", ".vscode", "dist", "build", ".mypy_cache", ".pytest_cache", ".solomon"}
IGNORE_LINES = [".venv/", "venv/", "node_modules/", "__pycache__/", "*.pyc", ".DS_Store", ".env", ".env.*", "*.pem", "*.key", ".solomon/"]
BROWSE_LIMIT = 400


def home() -> Path:
    return Path(os.environ.get("HOME") or Path.home()).resolve()


def within_home(path: Path) -> bool:
    try:
        path.resolve().relative_to(home())
        return True
    except ValueError:
        return False


# ------------------------------------------------------------------ browse
def browse(path: Optional[str], registered: set[str]) -> dict:
    """The sub-folders of `path` (default: the home folder). Hidden folders and anything outside home are never shown."""
    base = Path(path).expanduser() if path else home()
    try:
        base = base.resolve()
    except OSError as exc:
        raise ProjectError(400, f"폴더를 열 수 없습니다: {exc}") from exc
    if not within_home(base):
        raise ProjectError(403, "홈 폴더 밖은 볼 수 없습니다.")
    if not base.is_dir():
        raise ProjectError(404, "폴더가 아니거나 없습니다.")
    dirs, truncated = [], False
    try:
        entries = sorted(os.scandir(base), key=lambda e: e.name.casefold())
    except OSError as exc:
        raise ProjectError(403, f"이 폴더를 읽을 수 없습니다 (macOS의 폴더 접근 권한일 수 있습니다): {exc.strerror or exc}") from exc
    for e in entries:
        if e.name.startswith(".") or (base == home() and e.name in ("Library", "Applications")):
            continue
        try:
            if not e.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        if len(dirs) >= BROWSE_LIMIT:
            truncated = True
            break
        p = Path(e.path)
        dirs.append({"name": e.name, "path": str(p), "git": (p / ".git").exists(), "registered": str(p) in registered,
                     "solomon": (p / MARKER).is_file()})
    parent = str(base.parent) if base != home() else ""
    crumbs, cur = [], base
    while True:
        crumbs.append({"name": "홈" if cur == home() else cur.name, "path": str(cur)})
        if cur == home() or cur.parent == cur:
            break
        cur = cur.parent
    return {"path": str(base), "parent": parent, "dirs": dirs, "truncated": truncated, "crumbs": crumbs[::-1],
            "git": (base / ".git").exists(), "registered": str(base) in registered, "writable": os.access(base, os.W_OK)}


# ------------------------------------------------------------------ make a plain new folder (from the folder browser)
FOLDER_NAME = re.compile(r"^[^/\\\x00-\x1f.][^/\\\x00-\x1f]{0,79}$")


def make_folder(parent: str, name: str) -> Path:
    """A new empty folder inside `parent` (which must be inside the home folder). Only creates; never overwrites."""
    base = Path(parent or "").expanduser()
    try:
        base = base.resolve()
    except OSError as exc:
        raise ProjectError(400, f"폴더를 열 수 없습니다: {exc}") from exc
    if not within_home(base) or not base.is_dir():
        raise ProjectError(403, "홈 폴더 안의 폴더에서만 새 폴더를 만들 수 있습니다.")
    name = (name or "").strip()
    if not FOLDER_NAME.match(name) or name in (".", "..") or name.endswith("."):
        raise ProjectError(400, "폴더 이름에는 / \\ 를 쓸 수 없고, 점(.)으로 시작할 수 없으며, 80자 이하여야 합니다.")
    target = base / name
    if target.exists():
        raise ProjectError(409, f"같은 이름이 이미 있습니다: {name}")
    try:
        target.mkdir()
    except OSError as exc:
        raise ProjectError(403, f"폴더를 만들 수 없습니다 (쓰기 권한 확인): {exc.strerror or exc}") from exc
    return target


# ------------------------------------------------------------------ rename (only folders Solomon made)
def rename_project(path: Path, root: Path, new_name: str) -> Path:
    path = Path(path).resolve()
    if not is_managed(path, root):
        raise ProjectError(403, "이름 변경은 Solomon이 만든 프로젝트만 가능합니다. 다른 프로젝트는 '별명'을 쓰세요 (폴더는 건드리지 않습니다).")
    new_name = (new_name or "").strip()
    if not NAME_RE.match(new_name):
        raise ProjectError(400, "이름은 영문·숫자·점·밑줄·하이픈만, 60자 이하로 입력하세요.")
    if new_name == path.name:
        raise ProjectError(400, "현재 이름과 같습니다.")
    target = path.parent / new_name
    if target.exists():
        raise ProjectError(409, f"같은 이름의 폴더가 이미 있습니다: {target}")
    try:
        os.rename(path, target)
    except OSError as exc:
        raise ProjectError(500, f"이름을 바꾸지 못했습니다: {exc}") from exc
    if (target / ".git").exists():
        gitops.git(["worktree", "prune"], target, check=False)  # stale entries of finished runs point at the old path
    return target


# ------------------------------------------------------------------ git init for a plain folder
def _walk_count(path: Path, cap: int = 20000) -> tuple[int, list[str]]:
    count, skipped = 0, []
    for dirpath, dirnames, filenames in os.walk(path):
        keep = []
        for d in dirnames:
            if d in SKIP_DIRS or Path(dirpath, d).is_symlink():
                if dirpath == str(path) and d in SKIP_DIRS and d not in (".git", ".solomon"):
                    skipped.append(d)
                continue
            keep.append(d)
        dirnames[:] = keep
        for f in filenames:
            if f.startswith(".env") or f.endswith((".pem", ".key", ".pyc")) or f == ".DS_Store" or Path(dirpath, f).is_symlink():
                continue
            count += 1
            if count >= cap:
                return cap, skipped
    return count, skipped


def git_init_preview(path: Path) -> dict:
    path = Path(path)
    if (path / ".git").exists():
        raise ProjectError(409, "이미 git 저장소입니다.")
    n, skipped = _walk_count(path)
    n += 0 if (path / ".gitignore").exists() else 1  # the .gitignore Solomon writes is part of the first commit
    return {"files": n, "capped": n >= 20000, "excluded": sorted(set(skipped)) + ["비밀 파일(.env, *.pem, *.key)"]}


def git_init(path: Path) -> dict:
    """git init + a .gitignore that keeps secrets and bulky folders out + a first commit under the identity "Solomon"."""
    path = Path(path)
    if (path / ".git").exists():
        raise ProjectError(409, "이미 git 저장소입니다.")
    gi = path / ".gitignore"
    have = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    add = [l for l in IGNORE_LINES if l not in have]
    if add:
        with gi.open("a", encoding="utf-8") as fh:
            fh.write(("\n" if have and have[-1] else "") + "\n".join(add) + "\n")
    gitops.git(["init", "-q"], path)
    gitops.git(["symbolic-ref", "HEAD", "refs/heads/main"], path, check=False)
    gitops.git(["add", "-A"], path)
    gitops.git(["-c", "user.name=Solomon", "-c", "user.email=solomon@localhost", "commit", "-q", "--allow-empty", "-m", "Initial commit by Solomon"], path)
    gitops.exclude_from_git(path, ".solomon")
    n = len(gitops.git(["ls-files"], path).stdout.splitlines())
    return {"files": n}


# ------------------------------------------------------------------ open in Finder
def open_command() -> Optional[str]:
    return shutil.which("open") if sys.platform == "darwin" else shutil.which("xdg-open")


def open_path(path: Path) -> None:
    exe = open_command()
    if not exe:
        raise ProjectError(501, "이 컴퓨터에서는 폴더를 열 수 없습니다 (macOS의 Finder 또는 Linux의 xdg-open 필요).")
    subprocess.Popen([exe, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)


# ------------------------------------------------------------------ git identity (needed for commits from the dashboard)
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,120}\.[^@\s]{2,20}$")


def git_identity() -> dict:
    g = lambda k: gitops.git(["config", "--global", "--get", k], ".", check=False).stdout.strip()
    return {"name": g("user.name"), "email": g("user.email")}


def set_git_identity(name: str, email: str) -> dict:
    name, email = (name or "").strip(), (email or "").strip()
    if not name or len(name) > 60 or re.search(r"[\x00-\x1f]", name):
        raise ProjectError(400, "이름을 입력하세요 (60자 이하).")
    if not EMAIL.match(email):
        raise ProjectError(400, "이메일 형식이 올바르지 않습니다.")
    gitops.git(["config", "--global", "user.name", name], ".")
    gitops.git(["config", "--global", "user.email", email], ".")
    return git_identity()
