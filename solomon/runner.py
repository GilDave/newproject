"""Subprocess runner shared by every CLI call.

shell=False, prompt over stdin, hard timeout + no-output timeout, and the whole
process group is killed on timeout so a stuck CLI never leaves children behind.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional, Sequence

_ACTIVE: dict[int, subprocess.Popen] = {}
_LOCK = threading.Lock()


@dataclass
class ProcessResult:
    exit_code: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool
    duration_sec: float
    timeout_reason: Optional[str] = None


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":  # pragma: no cover - Windows is not a target of the MVP
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        deadline = time.time() + 2.0
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()


def kill_active() -> list[int]:
    """Stop every process the runner currently has alive. Returns the pids killed."""
    with _LOCK:
        procs = list(_ACTIVE.values())
    killed = []
    for proc in procs:
        kill_tree(proc)
        killed.append(proc.pid)
    return killed


def run_process(
    argv: Sequence[str],
    cwd: Optional[str] = None,
    env: Optional[dict[str, str]] = None,
    timeout: float = 300.0,
    no_output_timeout: Optional[float] = None,
    stdin_data: Optional[str] = None,
) -> ProcessResult:
    start = time.time()
    last_output = [start]
    out_chunks: list[str] = []
    err_chunks: list[str] = []

    proc = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=env,
        shell=False,
        stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=(os.name != "nt"),
    )
    with _LOCK:
        _ACTIVE[proc.pid] = proc

    def reader(pipe, sink):
        try:
            for line in iter(pipe.readline, ""):
                sink.append(line)
                last_output[0] = time.time()
        except (OSError, ValueError):
            pass
        finally:
            try:
                pipe.close()
            except OSError:
                pass

    def feeder():
        try:
            proc.stdin.write(stdin_data)
            proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass

    threads = [
        threading.Thread(target=reader, args=(proc.stdout, out_chunks), daemon=True),
        threading.Thread(target=reader, args=(proc.stderr, err_chunks), daemon=True),
    ]
    if stdin_data is not None:
        threads.append(threading.Thread(target=feeder, daemon=True))
    for t in threads:
        t.start()

    timed_out = False
    reason: Optional[str] = None
    try:
        while proc.poll() is None:
            now = time.time()
            if now - start >= timeout:
                timed_out, reason = True, f"HARD_TIMEOUT: exceeded {timeout}s"
                break
            if no_output_timeout and now - last_output[0] >= no_output_timeout:
                timed_out, reason = True, f"NO_OUTPUT_TIMEOUT: no output for {no_output_timeout}s"
                break
            time.sleep(0.05)
        if timed_out:
            kill_tree(proc)
    finally:
        with _LOCK:
            _ACTIVE.pop(proc.pid, None)

    for t in threads:
        t.join(timeout=1.0)
    return ProcessResult(
        exit_code=proc.poll(),
        stdout="".join(out_chunks),
        stderr="".join(err_chunks),
        timed_out=timed_out,
        duration_sec=round(time.time() - start, 3),
        timeout_reason=reason,
    )
