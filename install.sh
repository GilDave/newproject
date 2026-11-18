#!/usr/bin/env bash
# One-step install of Solomon from this folder (macOS / Linux).
#   bash install.sh
# Creates ./.venv (Python 3.11+), installs Solomon into it, then runs the zero-cost self-test and the preflight check.
# Safe to run again (e.g. after `git pull`). It never touches your projects and never uses an API key.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

say() { printf '%s\n' "$*"; }
fail() { say ""; say "✘ $*"; exit 1; }

# 1) a Python >= 3.11
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  p="$(command -v "$c" 2>/dev/null)" || continue
  if "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then PY="$p"; break; fi
done
if [ -z "$PY" ]; then
  say "Python 3.11 이상이 필요한데 찾지 못했습니다."
  if [ "$(uname)" = "Darwin" ]; then
    if command -v brew >/dev/null 2>&1; then fail "아래 명령을 실행한 뒤 이 스크립트를 다시 실행하세요:   brew install python@3.12"
    else fail "Homebrew가 없습니다. https://brew.sh 의 설치 명령을 먼저 실행하고, 이어서  brew install python@3.12  후 다시 실행하세요."; fi
  fi
  fail "python3.11+ 를 설치한 뒤 다시 실행하세요."
fi
say "Python: $PY ($("$PY" -c 'import platform; print(platform.python_version())'))"

command -v git >/dev/null 2>&1 || fail "git이 없습니다. 맥: xcode-select --install"

# 2) a fresh virtual environment (an old one made with an older Python is moved aside, not deleted)
if [ -x .venv/bin/python ] && ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  old=".venv.old-$(date +%Y%m%d-%H%M%S)"
  say "이전 가상환경(Python 3.11 미만)을 $old 로 옮깁니다."
  mv .venv "$old"
fi
[ -x .venv/bin/python ] || "$PY" -m venv .venv || fail "가상환경을 만들지 못했습니다."
.venv/bin/python -m pip install -q --upgrade pip >/dev/null 2>&1
say "Solomon을 설치합니다 (인터넷 연결 필요: pip 도구만 받으며 AI 서비스에는 접속하지 않습니다)…"
.venv/bin/python -m pip install -q -e . || fail "설치에 실패했습니다. 위 오류를 그대로 보내주세요."

chmod +x scripts/*.sh Start-Solomon.command Stop-Solomon.command install.sh 2>/dev/null

# 3) zero-cost checks
say ""
.venv/bin/solomon selftest || fail "자체 점검에 실패했습니다. 위 출력을 그대로 보내주세요."
say ""
.venv/bin/solomon doctor
rc=$?

say ""
say "설치 완료."
say "  시작:  bash scripts/start.sh        (또는 Finder에서 Start-Solomon.command 더블클릭)"
say "  종료:  bash scripts/stop.sh"
say "  가이드: 대시보드 우측 상단 '사용 가이드' 또는 USER_GUIDE.md"
[ "$rc" -eq 0 ] || say "※ 위 [missing]/[error] 항목은 사용 가이드 1장에 따라 먼저 해결하세요."
exit 0
