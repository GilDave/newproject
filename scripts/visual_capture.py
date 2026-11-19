#!/usr/bin/env python3
"""Screen-capture command for the visual gate: python3 scripts/visual_capture.py OUT_DIR

Starts the dashboard preview from THIS checkout (so a candidate's worktree shows the candidate), plays the flow preview in headless
Chromium and saves the stage screenshots into OUT_DIR. Exit codes: 0 ok, 3 the page is broken, 2 the capture could not run here.
Needs Node.js with Playwright (the same thing that builds the guide PDF).
"""
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "shots"
    node = shutil.which("node")
    if not node:
        print("node 를 찾지 못했습니다 (Node.js 설치 필요)")
        return 2
    env = os.environ.copy()
    root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip() if shutil.which("npm") else ""
    if root:
        env["NODE_PATH"] = root + (os.pathsep + env["NODE_PATH"] if env.get("NODE_PATH") else "")
    if subprocess.run([node, "-e", "require('playwright')"], env=env, capture_output=True).returncode != 0:
        print("Playwright 를 찾지 못했습니다 (npm i -g playwright)")
        return 2
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen([sys.executable, str(HERE / "ui_preview_server.py"), str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/?t=tok", timeout=1).read(1)
                break
            except OSError:
                if server.poll() is not None:
                    print("미리보기 서버가 시작되지 못했습니다: " + (server.stderr.read().decode()[-300:] if server.stderr else ""))
                    return 3  # the candidate's dashboard does not even start: that is the product being broken
                time.sleep(0.5)
        else:
            print("미리보기 서버가 응답하지 않습니다")
            return 3
        env["BASE"] = f"http://127.0.0.1:{port}"
        res = subprocess.run([node, str(HERE / "ui_snapshots.js"), out], env=env, capture_output=True, text=True, timeout=150)
        print((res.stdout + res.stderr).strip()[-400:])
        return res.returncode if res.returncode in (0, 3) else 2
    finally:
        server.terminate()
        try:
            server.wait(5)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    sys.exit(main())
