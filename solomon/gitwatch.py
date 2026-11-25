"""Per-project git location management and monitoring.

Read-mostly by design: status, ahead/behind, remotes, stash, worktrees and a few safety warnings are only *read*.
The only things this module changes are (1) a remote's address when the user asks for it and (2) the remote-tracking
refs through `git fetch` when the user asks for it. It never commits, pushes, pulls, merges, resets or deletes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from . import gitops

CMD_TIMEOUT = 8.0
FETCH_TIMEOUT = 60.0
STALE_FETCH_DAYS = 7
DIRTY_WARN = 30                     # this many changed files is "a lot of uncommitted work"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
SECRET_RE = re.compile(r"(^|/)(\.env(\.[^/]+)?|[^/]*\.pem|[^/]*\.key|id_rsa|id_ed25519|credentials\.json)$")
SECRET_OK = re.compile(r"(^|/)\.env\.(example|sample|template)$")
LEVEL_RANK = {"bad": 2, "warn": 1, "info": 0}
FP_MAX_FILES, FP_MAX_BYTES, FP_MAX_DIFF = 300, 20_000_000, 2_000_000     # 변경 지문을 계산할 최대 크기(넘으면 계산하지 않음)
VERSION_RE = re.compile(r"""^\s*(?:__version__|version|VERSION)\s*[:=]\s*["']([0-9][0-9A-Za-z.+_-]{0,40})["']""", re.M)


class GitWatchError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _env(extra: Optional[dict] = None) -> dict:
    env = dict(os.environ)
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    if extra:
        env.update(extra)
    return env


def _git(args: list, cwd: Path, timeout: float = CMD_TIMEOUT) -> Optional[str]:
    """stdout on success, None when git fails or times out (a broken repo must not break the overview)."""
    try:
        proc = gitops.git(args, cwd, check=False, timeout=timeout, env=_env())
    except Exception:
        return None
    return proc.stdout if proc.returncode == 0 else None


def mask_url(url: str) -> str:
    """Hide a password or token embedded in a remote address (https://user:token@host/…)."""
    try:
        parts = urlsplit(url)
        if parts.scheme in ("http", "https") and (parts.password or parts.username):
            host = parts.hostname or ""
            if parts.port:
                host += f":{parts.port}"
            return urlunsplit((parts.scheme, f"***@{host}", parts.path, parts.query, parts.fragment))
    except ValueError:
        pass
    return url


def valid_remote_url(url: str) -> bool:
    """https://…, ssh://…, git@host:owner/repo.git, or an existing local path. Never `ext::`, options or odd characters."""
    if not url or len(url) > 300 or url != url.strip() or any(ord(ch) < 32 or ch in " \t\n\\\"'`$;|&<>" for ch in url):
        return False
    if url.startswith("-") or "::" in url:
        return False
    if re.match(r"^(https?|ssh|git)://[A-Za-z0-9._~%@:+-]+(:\d+)?/[A-Za-z0-9._~%/@+-]+$", url):
        return True
    if re.match(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[A-Za-z0-9._~/+-]+$", url):
        return True
    return url.startswith("/") and ".." not in url.split("/")


def _remotes(path: Path) -> list[dict]:
    out = _git(["remote", "-v"], path) or ""
    seen: dict[str, dict] = {}
    for line in out.splitlines():
        m = re.match(r"^(\S+)\t(.+) \((fetch|push)\)$", line)
        if not m:
            continue
        name, url, kind = m.groups()
        r = seen.setdefault(name, {"name": name, "fetch": "", "push": ""})
        r[kind] = mask_url(url)
    return list(seen.values())


def inspect(path) -> dict:
    """One project's git picture. Always returns a dict with `state`: missing | none | empty | ready."""
    p = Path(path)
    info: dict = {"path": str(p), "state": "missing", "warnings": []}
    if not p.is_dir():
        return info
    top = _git(["rev-parse", "--show-toplevel"], p)
    if top is None:
        info["state"] = "none"
        return info
    top = top.strip()
    info.update(git_root=top, nested=Path(top).resolve() != p.resolve())
    gitdir = (_git(["rev-parse", "--absolute-git-dir"], p) or "").strip()
    info["git_dir"] = gitdir
    info["linked_worktree"] = bool(gitdir) and "/worktrees/" in gitdir.replace("\\", "/")
    if _git(["rev-parse", "--verify", "-q", "HEAD"], p) is None:
        info["state"] = "empty"
        info["remotes"] = _remotes(p)
        info["warnings"].append(_w("warn", "empty", "아직 커밋이 없습니다. 첫 커밋을 만들어야 개선·밤샘 수정을 쓸 수 있습니다."))
        return info
    info["state"] = "ready"
    sym = _git(["symbolic-ref", "--short", "-q", "HEAD"], p)
    info["branch"] = sym.strip() if sym else ""
    info["detached"] = not sym
    if not sym:
        info["branch"] = (_git(["rev-parse", "--short", "HEAD"], p) or "").strip()
    up = _git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], p)
    info["upstream"] = up.strip() if up else ""
    info["ahead"] = info["behind"] = None
    if info["upstream"]:
        lr = (_git(["rev-list", "--left-right", "--count", "HEAD...@{u}"], p) or "").split()
        if len(lr) == 2 and all(x.isdigit() for x in lr):
            info["ahead"], info["behind"] = int(lr[0]), int(lr[1])
    changed = untracked = conflicts = 0
    for line in (_git(["status", "--porcelain=v1"], p) or "").splitlines():
        code = line[:2]
        if code == "??":
            untracked += 1
        elif "U" in code or code in ("AA", "DD"):
            conflicts += 1
        elif line.strip():
            changed += 1
    info.update(changed=changed, untracked=untracked, conflicts=conflicts)
    last = (_git(["log", "-1", "--format=%h%x1f%an%x1f%at%x1f%s"], p) or "").rstrip("\n").split("\x1f")
    if len(last) == 4 and last[2].isdigit():
        info["last_commit"] = {"hash": last[0], "author": last[1], "time": int(last[2]), "subject": last[3][:120]}
    head = (_git(["rev-parse", "HEAD"], p) or "").strip()
    info["head"] = head
    tag = (_git(["describe", "--tags", "--abbrev=0"], p) or "").strip()
    info["tag"] = tag
    info["app_version"] = app_version(p)
    info["fingerprint"] = fingerprint(p, head) if (changed + untracked) else None
    week = (_git(["rev-list", "--count", "--since=7.days", "HEAD"], p) or "").strip()
    info["commits_7d"] = int(week) if week.isdigit() else None
    info["stash"] = len((_git(["stash", "list"], p) or "").splitlines())
    wt = (_git(["worktree", "list", "--porcelain"], p) or "").count("worktree ")
    info["worktrees"] = max(0, wt - 1)
    info["remotes"] = _remotes(p)
    fh = Path(gitdir) / "FETCH_HEAD" if gitdir else None
    info["fetched_at"] = int(fh.stat().st_mtime) if fh and fh.exists() else None
    tracked = [f for f in (_git(["ls-files"], p) or "").splitlines() if SECRET_RE.search(f) and not SECRET_OK.search(f)]
    info["tracked_secrets"] = tracked[:5]
    info["warnings"] = warnings_for(info)
    return info


def app_version(root: Path) -> str:
    """프로젝트가 스스로 밝힌 버전(pyproject.toml · package.json · VERSION · 패키지의 __init__.py). 없으면 ''."""
    def read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:200_000] if path.is_file() else ""
        except OSError:
            return ""
    m = re.search(r'^version\s*=\s*"([^"]{1,40})"', read(root / "pyproject.toml"), re.M)
    if m:
        return m.group(1)
    try:
        v = json.loads(read(root / "package.json") or "{}").get("version")
        if isinstance(v, str) and v:
            return v[:40]
    except (ValueError, AttributeError):
        pass
    first = read(root / "VERSION").strip().splitlines()[:1]
    if first and re.match(r"^[0-9][0-9A-Za-z.+_-]{0,40}$", first[0].strip()):
        return first[0].strip()
    try:
        for d in sorted(x for x in root.iterdir() if x.is_dir() and not x.name.startswith((".", "_")))[:40]:
            m = VERSION_RE.search(read(d / "__init__.py"))
            if m:
                return m.group(1)
    except OSError:
        pass
    return ""


def fingerprint(p: Path, head: str) -> Optional[str]:
    """같은 커밋 위의 '커밋하지 않은 변경'이 두 폴더에서 같은지 비교하는 짧은 지문(7자). 새 파일은 내용까지 해시한다. 너무 크면 None."""
    status = _git(["status", "--porcelain=v1"], p) or ""
    diff = _git(["diff", "HEAD", "--binary"], p) or ""
    if len(diff) > FP_MAX_DIFF:
        return None
    files = [f for f in (_git(["ls-files", "--others", "--exclude-standard"], p) or "").splitlines() if f]
    if len(files) > FP_MAX_FILES:
        return None
    h = hashlib.sha1()
    h.update(head.encode())
    h.update(status.encode())
    h.update(diff.encode("utf-8", "replace"))
    total = 0
    base = Path((_git(["rev-parse", "--show-toplevel"], p) or str(p)).strip())
    for f in sorted(files):
        try:
            fp = base / f
            size = fp.stat().st_size
            total += size
            if total > FP_MAX_BYTES:
                return None
            h.update(f.encode())
            with fp.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
        except OSError:
            h.update(b"?" + f.encode())
    return h.hexdigest()[:7]


def _w(level: str, code: str, text: str) -> dict:
    return {"level": level, "code": code, "text": text}


def warnings_for(i: dict) -> list[dict]:
    w: list[dict] = []
    if i.get("tracked_secrets"):
        w.append(_w("bad", "secret", "비밀이 들어 있을 수 있는 파일이 git에 추적되고 있습니다: " + ", ".join(i["tracked_secrets"][:3]) + " — 원격에 올리기 전에 추적을 끊으세요."))
    if i.get("conflicts"):
        w.append(_w("bad", "conflict", f"충돌 중인 파일이 {i['conflicts']}개 있습니다."))
    if not i.get("remotes"):
        w.append(_w("warn", "no_remote", "원격 저장소가 없습니다. 이 컴퓨터가 고장 나면 코드가 사라집니다(백업 없음)."))
    elif not i.get("upstream") and not i.get("detached"):
        w.append(_w("warn", "no_upstream", "이 브랜치는 원격 브랜치와 연결돼 있지 않아 올렸는지 알 수 없습니다."))
    if i.get("behind"):
        w.append(_w("warn", "behind", f"원격에 내가 받지 않은 커밋이 {i['behind']}개 있습니다(다른 AI·사람이 올린 변경)."))
    if i.get("ahead"):
        w.append(_w("warn", "ahead", f"아직 원격에 올리지 않은 커밋이 {i['ahead']}개 있습니다."))
    if i.get("changed", 0) + i.get("untracked", 0) >= DIRTY_WARN:
        w.append(_w("warn", "dirty", f"커밋하지 않은 변경이 {i['changed'] + i['untracked']}개로 많습니다."))
    if i.get("detached"):
        w.append(_w("warn", "detached", "특정 커밋을 보고 있는 상태(detached HEAD)입니다. 여기서 만든 커밋은 브랜치에 남지 않습니다."))
    fa = i.get("fetched_at")
    if i.get("upstream") and fa and time.time() - fa > STALE_FETCH_DAYS * 86400:
        w.append(_w("info", "stale", f"원격 정보를 {STALE_FETCH_DAYS}일 넘게 새로 받지 않았습니다. '원격 확인'을 눌러 최신으로 맞추세요."))
    if i.get("nested"):
        w.append(_w("info", "nested", f"이 폴더는 상위 저장소({i.get('git_root')})의 일부입니다."))
    w.sort(key=lambda x: -LEVEL_RANK[x["level"]])
    return w


def summarize(infos: list[dict]) -> dict:
    ready = [i for i in infos if i.get("state") == "ready"]
    return {"projects": len(infos), "no_git": sum(1 for i in infos if i.get("state") in ("none", "missing")),
            "no_remote": sum(1 for i in ready if not i.get("remotes")), "dirty": sum(1 for i in ready if i.get("changed") or i.get("untracked")),
            "unpushed": sum(1 for i in ready if i.get("ahead")), "behind": sum(1 for i in ready if i.get("behind")),
            "bad": sum(1 for i in infos if any(w["level"] == "bad" for w in i.get("warnings", [])))}


def set_remote(path, name: str, url: str) -> dict:
    """Add the remote, or change its address when it exists. The address is validated; nothing is contacted."""
    p = Path(path)
    name, url = (name or "").strip(), (url or "").strip()
    if not NAME_RE.match(name):
        raise GitWatchError(400, "원격 이름은 영문·숫자·점·밑줄·하이픈만 쓸 수 있습니다 (보통 origin).")
    if not valid_remote_url(url):
        raise GitWatchError(400, "원격 주소 형식이 올바르지 않습니다. 예: https://github.com/계정/저장소.git 또는 git@github.com:계정/저장소.git")
    if _git(["rev-parse", "--is-inside-work-tree"], p) is None:
        raise GitWatchError(400, "git 저장소가 아닙니다. 먼저 'git 시작하기'를 하세요.")
    exists = name in {r["name"] for r in _remotes(p)}
    proc = gitops.git(["remote", "set-url" if exists else "add", name, url], p, check=False, timeout=CMD_TIMEOUT, env=_env())
    if proc.returncode != 0:
        raise GitWatchError(500, f"원격 설정에 실패했습니다: {(proc.stderr or proc.stdout).strip()[:200]}")
    return {"ok": True, "message": f"원격 '{name}' 주소를 {'바꿨습니다' if exists else '추가했습니다'}: {mask_url(url)}. 아직 연결은 시도하지 않았습니다. '원격 확인'으로 접속을 시험하세요."}


def fetch(path, remote: str = "") -> dict:
    """`git fetch --prune`: only updates remote-tracking refs. Your files, branches and commits are not touched."""
    p = Path(path)
    remote = (remote or "").strip()
    names = [r["name"] for r in _remotes(p)]
    if not names:
        raise GitWatchError(400, "원격 저장소가 없습니다. 먼저 원격 주소를 등록하세요.")
    if remote and remote not in names:
        raise GitWatchError(400, "알 수 없는 원격 이름입니다.")
    target = [remote] if remote else ["--all"]
    env = _env({"GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=15"})
    try:
        proc = gitops.git(["fetch", "--prune", *target], p, check=False, timeout=FETCH_TIMEOUT, env=env)
    except Exception as exc:
        raise GitWatchError(504, f"원격에 연결하지 못했습니다(시간 초과 등): {type(exc).__name__}") from exc
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).strip().splitlines()
        raise GitWatchError(502, "원격에서 받아오지 못했습니다: " + (mask_url(msg[-1]) if msg else "알 수 없는 오류")
                            + " (주소·접근 권한·로그인 상태를 확인하세요)")
    return {"ok": True, "message": "원격 정보를 새로 받았습니다(내 파일과 브랜치는 그대로)."}
