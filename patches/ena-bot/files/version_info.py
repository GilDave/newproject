"""대시보드에 보여줄 '지금 이 코드는 어떤 버전인가' 정보(읽기 전용). Solomon 프로젝트 카드의 버전 줄과 같은 값(커밋 · 변경 지문)을 낸다.

- app_version: pyproject.toml · package.json · VERSION · 패키지의 __version__ 에서 찾는다(없으면 '').
- commit / branch / tag / dirty / fingerprint: git 에서 읽는다. 지문은 '같은 커밋 위의 커밋하지 않은 변경'이 같은지 비교하는 7자 값이다.
- stale_code: 서버가 시작한 뒤 ena_bot/*.py 파일이 바뀌었으면 True → 실행 중인 코드가 디스크의 코드와 다르니 서비스를 재시작해야 한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = Path(__file__).resolve().parent
FP_MAX_FILES, FP_MAX_BYTES, FP_MAX_DIFF = 300, 20000000, 2000000
VERSION_RE = re.compile(r"""^\s*(?:__version__|version|VERSION)\s*[:=]\s*["']([0-9][0-9A-Za-z.+_-]{0,40})["']""", re.M)
TTL_SEC = 10
_LOCK = threading.Lock()
_CACHE = {}


def _git(args, cwd):
    env = dict(os.environ)
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    try:
        proc = subprocess.run(["git", "-c", "core.quotepath=false"] + list(args), cwd=str(cwd), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=8, env=env)
    except Exception:  # noqa: BLE001
        return None
    return proc.stdout if proc.returncode == 0 else None


def app_version(root=ROOT):
    root = Path(root)

    def read(path):
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:200000] if path.is_file() else ""
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
    m = VERSION_RE.search(read(root / "ena_bot" / "__init__.py"))
    return m.group(1) if m else ""


def fingerprint(p, head):
    """Solomon(gitwatch.fingerprint)과 같은 계산이라 두 화면의 값을 그대로 비교할 수 있다."""
    p = Path(p)
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
            total += fp.stat().st_size
            if total > FP_MAX_BYTES:
                return None
            h.update(f.encode())
            with fp.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
        except OSError:
            h.update(b"?" + f.encode())
    return h.hexdigest()[:7]


def code_stamp(pkg=PKG):
    """ena_bot/*.py 의 (이름·수정시각·크기)를 합친 값. 서버 시작 때의 값과 달라지면 코드가 바뀐 것."""
    h = hashlib.sha1()
    try:
        for f in sorted(Path(pkg).glob("*.py")):
            st = f.stat()
            h.update(("%s:%d:%d;" % (f.name, st.st_mtime_ns, st.st_size)).encode())
    except OSError:
        return ""
    return h.hexdigest()


STARTED_AT = int(time.time())
STARTED_STAMP = code_stamp()


def info(root=ROOT, pkg=PKG, started_stamp=None, started_at=None, now=None):
    now = time.time() if now is None else now
    out = {"app_version": app_version(root), "started_at": int(STARTED_AT if started_at is None else started_at), "git": False}
    head = (_git(["rev-parse", "HEAD"], root) or "").strip()
    if head:
        status = [l for l in (_git(["status", "--porcelain=v1"], root) or "").splitlines() if l.strip()]
        sym = (_git(["symbolic-ref", "--short", "-q", "HEAD"], root) or "").strip()
        out.update(git=True, commit=head[:7], head=head, branch=sym or "(detached)", tag=(_git(["describe", "--tags", "--abbrev=0"], root) or "").strip(),
                   dirty=len(status), fingerprint=fingerprint(root, head) if status else None)
    ref = STARTED_STAMP if started_stamp is None else started_stamp
    out["stale_code"] = bool(ref) and code_stamp(pkg) != ref
    return out


def payload(ttl=TTL_SEC):
    now = time.time()
    with _LOCK:
        hit = _CACHE.get("v")
        if hit and now - hit[0] < ttl:
            return hit[1]
        body = info()
        _CACHE["v"] = (now, body)
    return body
