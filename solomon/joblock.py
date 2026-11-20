"""One Solomon job at a time on this computer, across processes.

The dashboard remembers its own job in memory only. A restarted dashboard, a second dashboard, or a run started from
the terminal would not know about a job that is still running. So every job process holds an OS file lock
(``~/.solomon/job.lock``); the operating system drops it automatically when the process ends, however it ends.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore

JOB_COMMANDS = {"run", "analyze", "autofix", "video"}
BUSY = "이미 실행 중인 작업이 있습니다 (터미널이나 다른/이전 대시보드에서 시작된 작업). 끝나거나 중지한 뒤 다시 시도하세요."


def lock_path() -> Path:
    return Path.home() / ".solomon" / "job.lock"


def _open():
    p = lock_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    return open(p, "a+")


def acquire():
    """Take the lock for this process; returns a handle to keep alive, or None when someone else holds it."""
    if fcntl is None:
        return object()
    f = _open()
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    return f


def is_busy() -> bool:
    """True when another process holds the lock (released again at once when it was free)."""
    if fcntl is None:
        return False
    try:
        f = _open()
    except OSError:
        return False
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    else:
        fcntl.flock(f, fcntl.LOCK_UN)
        return False
    finally:
        f.close()
