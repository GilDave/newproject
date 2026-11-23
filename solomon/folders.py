"""Folder operations the dashboard does for you, so the terminal is not needed: browse, rename, git init, open in Finder.

Everything is limited on purpose: browsing stays inside the user's home folder, renaming is only for projects Solomon
created, `git init` never commits secret files, and "open" only opens things that belong to a registered project."""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
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
CAP = 20000
BIG_BYTES = 5 * 1024 * 1024
DATA_NAMES = ("*.db", "*.sqlite", "*.sqlite3", "*.sqlite-wal", "*.sqlite-shm", "*.log", "*.bak", "*.dump", "*.pid")
DATA_DIRS = {"backup", "backups", "logs", "log", "tmp", ".cache"}
CONFIGISH = {".conf", ".cfg", ".ini", ".toml", ".yaml", ".yml", ".json", ".properties", ".sh", ".bash", ".zsh", ".env", ".plist", ".xml", ".txt"}
SECRET_VALUE = re.compile(r"(?i)(pass(?:word|wd)?|secret|token|api[_-]?key|private[_-]?key)\w*[\"']?\s*[:=]\s*[\"']?[^\s\"'$(){}<>\[\],;#]{4,}")
SCAN_READS = 3000  # how many config-like files are opened to look for secret values
MAX_READ = 200 * 1024
KIND_LABEL = {"secret": "비밀", "data": "데이터·로그", "big": "큰 파일"}


def git_state(path: Path) -> str:
    """none = no repository, empty = a repository without a commit, ready = has commits."""
    path = Path(path)
    if not (path / ".git").exists():
        return "none"
    return "ready" if gitops.git(["rev-parse", "--verify", "HEAD"], path, check=False).returncode == 0 else "empty"


def _classify(base: Path, rel: str, reads: list[int]) -> Optional[tuple[str, str]]:
    """Why a file should stay out of a first commit, or None. Names are checked first, then the contents of small config-like files."""
    if gitops._is_secret(rel):
        return "secret", "비밀 파일 이름 (.env, *.pem, *.key …)"
    full = base / rel
    try:
        size = full.stat().st_size
    except OSError:
        return None
    name = rel.rsplit("/", 1)[-1]
    if Path(name).suffix.lower() in CONFIGISH and size <= MAX_READ and reads[0] < SCAN_READS:
        reads[0] += 1
        try:
            if SECRET_VALUE.search(full.read_text(encoding="utf-8", errors="ignore")):
                return "secret", "비밀번호·토큰처럼 보이는 값이 들어 있음"
        except OSError:
            pass
    if size > BIG_BYTES:
        return "big", "5MB보다 큼"
    if any(fnmatch.fnmatch(name, pat) for pat in DATA_NAMES) or any(part in DATA_DIRS for part in rel.split("/")[:-1]):
        return "data", "데이터베이스·로그·백업 (기록할 코드가 아님)"
    return None


def _scan(path: Path) -> dict:
    """Everything a first commit would hold: the files to commit, the files kept out and why, nested repositories, skipped folders."""
    path = Path(path)
    state = git_state(path)
    rels: list[str] = []
    skipped: list[str] = []
    nested: list[str] = []
    capped = False
    if state == "empty":  # a repository exists: respect its own .gitignore
        out = gitops.git(["ls-files", "-co", "--exclude-standard", "-z"], path).stdout.split("\0")
        rels = [f for f in out if f]
        capped = len(rels) >= CAP
    else:
        for dirpath, dirnames, filenames in os.walk(path):
            keep = []
            for d in dirnames:
                full = Path(dirpath, d)
                if d in SKIP_DIRS or full.is_symlink():
                    if dirpath == str(path) and d in SKIP_DIRS and d not in (".git", ".solomon"):
                        skipped.append(d)
                    continue
                if (full / ".git").exists():  # a repository inside: git would record it as an empty link, so it stays out
                    nested.append(str(full.relative_to(path)))
                    continue
                keep.append(d)
            dirnames[:] = keep
            for f in filenames:
                if f.endswith(".pyc") or f == ".DS_Store" or Path(dirpath, f).is_symlink():
                    continue
                rels.append(str(Path(dirpath, f).relative_to(path)))
                if len(rels) >= CAP:
                    capped = True
                    break
            if capped:
                break
    reads = [0]
    flagged: dict[str, tuple[str, str]] = {}
    clean: list[str] = []
    for rel in sorted(rels):
        why = _classify(path, rel, reads)
        if why:
            flagged[rel] = why
        else:
            clean.append(rel)
    return {"state": state, "clean": clean, "flagged": flagged, "nested": sorted(nested), "skipped": sorted(set(skipped)), "capped": capped}


def _ignore_line(rel: str) -> str:
    return "/" + re.sub(r"([*?\[\]\\])", r"\\\1", rel)


def _manual_commands(path: Path, scan: dict) -> str:
    lines = [f"cd {shlex.quote(str(path))}"]
    if scan["state"] == "none":
        lines.append("git init")
    keep = [_ignore_line(r) for r in scan["flagged"]] + [n + "/" for n in scan["nested"]]
    if keep:
        shown = keep[:25]
        lines.append("printf '%s\\n' " + " ".join(shlex.quote(x) for x in shown) + " >> .gitignore" + ("   # 나머지 %d개도 같은 방식으로" % (len(keep) - 25) if len(keep) > 25 else ""))
    lines += ["git add -A", "git status --short   # 비밀 파일이 보이면 커밋하지 말고 git restore --staged <파일>", 'git commit -m "초기 커밋"']
    return "\n".join(lines)


def _guard(path: Path) -> None:
    resolved = path.resolve()
    if resolved == home() or resolved == Path(resolved.anchor):
        raise ProjectError(400, "홈 폴더나 최상위 폴더 전체는 git 저장소로 만들지 않습니다. 프로젝트 폴더를 고르세요.")


def git_init_preview(path: Path) -> dict:
    path = Path(path)
    _guard(path)
    scan = _scan(path)
    if scan["state"] == "ready":
        raise ProjectError(409, "이미 git 저장소입니다.")
    have_ignore = (path / ".gitignore").exists()
    n = len(scan["clean"]) + (0 if have_ignore else 1)  # the .gitignore Solomon writes is part of the first commit
    flagged = [{"path": r, "kind": k, "why": w} for r, (k, w) in scan["flagged"].items()]
    return {"state": scan["state"], "files": n, "capped": scan["capped"],
            "excluded": scan["skipped"] + scan["nested"] + ["비밀 파일(.env, *.pem, *.key)"],
            "flagged": flagged[:200], "flagged_total": len(flagged), "nested": scan["nested"],
            "sample": scan["clean"][:30], "commands": _manual_commands(path, scan), "name": path.name}


def git_init(path: Path, include: Optional[list] = None) -> dict:
    """git init (or the first commit of a repository that has none) + a .gitignore that keeps secrets, data and bulky folders out,
    under the identity "Solomon". `include` names flagged files the user chose to commit anyway."""
    path = Path(path)
    _guard(path)
    scan = _scan(path)
    if scan["state"] == "ready":
        raise ProjectError(409, "이미 git 저장소입니다.")
    if scan["capped"]:
        raise ProjectError(400, f"파일이 {CAP}개 이상이라 프로젝트 폴더가 아닌 것 같습니다. 프로젝트 폴더를 골라 주세요.")
    allow = {str(x) for x in (include or [])}
    out_files = [r for r in scan["flagged"] if r not in allow]
    gi = path / ".gitignore"
    have = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    add = [l for l in IGNORE_LINES if l not in have]
    extra = [l for l in [_ignore_line(r) for r in out_files] + [n + "/" for n in scan["nested"]] if l not in have and l not in add]
    if extra:
        add += ["# Solomon: 첫 커밋에서 제외한 파일(비밀·데이터·큰 파일)"] + extra
    if add:
        with gi.open("a", encoding="utf-8") as fh:
            fh.write(("\n" if have and have[-1] else "") + "\n".join(add) + "\n")
    if scan["state"] == "none":
        gitops.git(["init", "-q"], path)
        gitops.git(["symbolic-ref", "HEAD", "refs/heads/main"], path, check=False)
    else:  # something was staged before (maybe a secret): start from an empty index
        gitops.git(["rm", "-r", "--cached", "-q", "--ignore-unmatch", "."], path, check=False)
    gitops.git(["add", "-A"], path)
    staged = set(gitops.git(["diff", "--cached", "--name-only", "-z"], path).stdout.split("\0"))
    leaked = sorted(r for r in out_files if r in staged)
    if leaked:  # the ignore rules did not hold: stop before anything is recorded
        gitops.git(["rm", "-r", "--cached", "-q", "--ignore-unmatch", "."], path, check=False)
        raise ProjectError(500, "제외해야 할 파일이 커밋 대상에 들어가 중단했습니다: " + ", ".join(leaked[:5]))
    gitops.git(["-c", "user.name=Solomon", "-c", "user.email=solomon@localhost", "commit", "-q", "--allow-empty", "-m", "Initial commit by Solomon"], path)
    gitops.exclude_from_git(path, ".solomon")
    n = len(gitops.git(["ls-files"], path).stdout.splitlines())
    return {"files": n, "excluded": len(out_files), "excluded_list": out_files[:20]}


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
